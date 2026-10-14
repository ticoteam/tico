//! Task lanes (task_lanes_v2): a task-keyed turn runs in its own lane beside the bot's other work, one turn at
//! a time per (bot, lane) on this computer (sessions::Lanes; runner/worktrees.py lane_lock). The claim names
//! the lanes busy here (`busy_lanes`); the heartbeat advertises the capabilities, each dropped for ten minutes
//! by a server that refuses it.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::util::text_of;

pub const CAPABILITIES: &[&str] = &["task_lanes_v2", "session_roots_v1", "task_goals_v1"];
const DROP_FOR: Duration = Duration::from_secs(600);

#[derive(Clone, Debug)]
pub struct Running {
    pub runtimes: BTreeSet<String>,
}

fn running() -> &'static Mutex<HashMap<String, Running>> {
    static R: OnceLock<Mutex<HashMap<String, Running>>> = OnceLock::new();
    R.get_or_init(Default::default)
}

/// Note a claimed attempt's lane and the runtimes it may use (its own and its fallback's).
pub fn track(attempt: &Value) {
    let config = &attempt["config"];
    let mut runtimes: BTreeSet<String> = [text_of(&config["runtime"])].into_iter().collect();
    if let Some(hop) = crate::fallback::configured(config) {
        runtimes.insert(hop.runtime);
    }
    runtimes.remove("");
    let row = Running { runtimes };
    running().lock().unwrap().insert(text_of(&attempt["id"]), row);
}

pub fn forget(aid: &str) {
    running().lock().unwrap().remove(aid);
}

#[allow(dead_code)] // the harness installer's mid-turn guard reads it
pub fn runtimes_in_use() -> BTreeSet<String> {
    running().lock().unwrap().values().flat_map(|r| r.runtimes.iter().cloned()).collect()
}

fn dropped() -> &'static Mutex<HashMap<String, Instant>> {
    static D: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();
    D.get_or_init(Default::default)
}

/// Whether a heartbeat or claim field is held back from a server that refused it in the last ten minutes.
pub fn held_back(name: &str) -> bool {
    dropped().lock().unwrap().get(name).is_some_and(|at| at.elapsed() < DROP_FOR)
}

pub fn hold_back(name: &str) {
    dropped().lock().unwrap().insert(name.into(), Instant::now());
}

/// The capabilities this heartbeat advertises.
pub fn capabilities() -> Vec<&'static str> {
    CAPABILITIES.iter().copied().filter(|c| !held_back(c)).collect()
}

/// What to leave out after a heartbeat's 422 (service.py maintain): the newest refused capability, else the
/// `profiles` report, else nothing (the refusal stands).
pub fn refused_field(detail: &str, body: &Value) -> Option<String> {
    let caps: Vec<String> = body["capabilities"].as_array().into_iter().flatten().map(text_of).collect();
    if detail.contains("capabilities") {
        for name in ["task_goals_v1", "session_roots_v1", "task_lanes_v2"] {
            if caps.iter().any(|c| c == name) {
                return Some(name.into());
            }
        }
    }
    let extra = detail.to_lowercase().contains("extra");
    if body.get("profiles").is_some() && (detail.contains("profiles") || (extra && detail.contains("weekly"))) {
        return Some("profiles".into());
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn task_turns_get_their_own_lane() {
        let lane = crate::sessions::turn_lane;
        assert_eq!(lane(&json!({"bot": "ops", "config": {}, "task": {"id": "t1"}})), "task:t1");
        assert_eq!(lane(&json!({"bot": "ops", "config": {}, "lane": "task:t9"})), "task:t9");
        assert_eq!(lane(&json!({"bot": "ops", "config": {}, "conversation": {"id": "c"}})), "main");
        track(&json!({"id": "a1", "bot": "ops", "config": {"runtime": "codex", "fallback": {"harness": "claude", "model": "m"}}, "task": {"id": "t1"}}));
        track(&json!({"id": "a2", "bot": "ops", "config": {"runtime": "codex"}}));
        assert!(runtimes_in_use().contains("claude"));
        forget("a1");
        forget("a2");
        assert_eq!(refused_field("body.capabilities: bad", &json!({"capabilities": ["task_lanes_v2", "task_goals_v1"]})).as_deref(), Some("task_goals_v1"));
    }
}
