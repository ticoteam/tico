//! Where this runner panicked, for the install's opt-in error reporting (docs/observability.md). A panic hook keeps
//! the file and line of each panic in this crate, never its message (that can hold a task's or a person's text), and
//! the next heartbeat hands them to the hub (`POST runners/errors`), which drops them unless reporting is on and
//! scrubs what it sends. The runner itself never talks to Sentry.

use std::sync::{Mutex, TryLockError};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::client::Client;

/// Panics held until the next heartbeat; more are dropped, a runner panicking in a loop reports the first ones.
const HELD: usize = 5;

/// The same file and line again within this long goes unreported: a panic in a loop is one report an hour, not hundreds.
const REPEAT: Duration = Duration::from_secs(3600);

static PENDING: Mutex<Vec<(String, u32)>> = Mutex::new(Vec::new());
static NOTED: Mutex<Vec<(String, u32, Instant)>> = Mutex::new(Vec::new());

/// Note each panic's location, then let the hook that was there (the default one prints it) run as before.
pub fn install() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(at) = info.location() {
            note(at.file(), at.line());
        }
        previous(info);
    }));
}

fn note(file: &str, line: u32) {
    // A panic in a dependency names a path outside this crate (a registry folder can name the user): it goes without one.
    let file = file.strip_prefix("runner-rs/").unwrap_or(file);
    let own = file.starts_with("src/") && file.ends_with(".rs") && !file.contains("..");
    let at = if own { (file.to_string(), line) } else { (String::new(), 0) };
    // try_lock: a panic while a list is held must not wait on itself. A poisoned list is still a list.
    let (Some(mut noted), Some(mut held)) = (locked(&NOTED), locked(&PENDING)) else { return };
    let now = Instant::now();
    noted.retain(|(_, _, when)| now.duration_since(*when) < REPEAT);
    if held.len() < HELD && !noted.iter().any(|(f, l, _)| (f, *l) == (&at.0, at.1)) {
        noted.push((at.0.clone(), at.1, now));
        held.push(at);
    }
}

fn locked<T>(m: &Mutex<T>) -> Option<std::sync::MutexGuard<'_, T>> {
    match m.try_lock() {
        Ok(guard) => Some(guard),
        Err(TryLockError::Poisoned(e)) => Some(e.into_inner()),
        Err(TryLockError::WouldBlock) => None,
    }
}

fn take() -> Vec<(String, u32)> {
    std::mem::take(&mut *PENDING.lock().unwrap_or_else(|e| e.into_inner()))
}

fn body(held: &[(String, u32)]) -> Value {
    let panics: Vec<Value> = held.iter().map(|(file, line)| json!({"file": file, "line": line})).collect();
    json!({"version": crate::runner::RUNNER_VERSION, "panics": panics})
}

/// Send what panicked since the last heartbeat. Best effort: a hub from before this route answers 404 and they are gone.
pub async fn report(client: &Client) {
    let held = take();
    if !held.is_empty() {
        let _ = client.post("runners/errors", &body(&held), None).await;
    }
}

/// Tests that note panics hold this: the lists are the process's own.
#[cfg(test)]
pub(crate) static TEST_LOCK: Mutex<()> = Mutex::new(());

#[cfg(test)]
pub(crate) fn note_for_test(file: &str, line: u32) {
    note(file, line);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_only_a_location_in_this_crate_once_an_hour_and_at_most_five() {
        let _one = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _ = take();
        note("src/runner.rs", 812);
        note("src/runner.rs", 812);
        note("runner-rs/src/hosts/mac.rs", 7);
        note("/home/ana/.cargo/registry/src/index.crates.io-1/tokio-1.0.0/src/task.rs", 9);
        let held = take();
        assert!(take().is_empty());
        // Tests never install the hook, so nothing else lands in the list meanwhile.
        assert_eq!(body(&held)["panics"], json!([
            {"file": "src/runner.rs", "line": 812}, {"file": "src/hosts/mac.rs", "line": 7}, {"file": "", "line": 0}]));
        assert_eq!(body(&held)["version"], crate::runner::RUNNER_VERSION);
        // A seen location stays quiet after it was sent, and a burst of new ones keeps the first five.
        note("src/runner.rs", 812);
        for line in 900..920 {
            note("src/lanes.rs", line);
        }
        assert_eq!(take().iter().map(|(_, line)| *line).collect::<Vec<_>>(), vec![900, 901, 902, 903, 904]);
    }
}
