//! Task lanes (task_lanes_v2): a task-keyed turn runs in its own lane beside the bot's other work, one turn at
//! a time per (bot, lane) on this computer (sessions::Lanes; runner/worktrees.py lane_lock). The claim names
//! the lanes busy here (`busy_lanes`); the heartbeat advertises the capabilities, each dropped for ten minutes
//! by a server that refuses it.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::util::text_of;

pub const CAPABILITIES: &[&str] = &[
    "task_lanes_v2", "session_roots_v1", "task_goals_v1", "capacity_continue_v1", "source_continue_v1",
    crate::login::SIGN_OUT_CAPABILITY, crate::login::KEYS_CAPABILITY, crate::login::SWITCH_TYPE_CAPABILITY,
    crate::login::REMOVE_CAPABILITY, crate::login::SWITCH_TO_KEY_CAPABILITY, "turns_at_once_v1",
    // Private live meetings run in their own lane and checkout (sessions.rs); the server hands them to no other runner.
    "private_lanes_v1",
    "model_subscriptions_v1",
    // Turns of a bot with a browser exit go through it (browser_exit.rs); the server hands them to no other runner.
    "browser_exit_v1",
    "memory_tidy_packets_v1",
];
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

#[cfg(test)]
pub fn release(name: &str) {
    dropped().lock().unwrap().remove(name);
}

/// The capabilities this heartbeat advertises.
pub fn capabilities() -> Vec<&'static str> {
    let screen = crate::screen::offers(crate::screen::readiness().as_ref()).then_some(crate::screen::CAPABILITY);
    CAPABILITIES.iter().copied().chain(screen).filter(|c| !held_back(c)).collect()
}

/// Root-level heartbeat fields an older server may refuse; each is left out for ten minutes after a refusal.
const ROOT_FIELDS: &[&str] = &["worktrees", "profiles", "repositories", "restart"];

fn rows<'a>(body: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    body[key].as_array().into_iter().flatten()
}

fn has_in_profiles(body: &Value, key: &str) -> bool {
    rows(body, "profiles").any(|p| p["runtimes"].as_object().is_some_and(|r| r.values().any(|s| s.get(key).is_some())))
}


/// What to leave out after a heartbeat's 422 (service.py _report_heartbeat), as a hold name: only `weekly` of the
/// profile report, the worktree reports' `record_updated` stamp, the newest refused capability, else a root field
/// the detail names (or, for a bare "extra" with no path, the first one present). None: the refusal stands.
pub fn refused_field(detail: &str, body: &Value) -> Option<String> {
    let extra = detail.to_lowercase().contains("extra");
    // Older servers keep receiving the sign-in report without the field they refuse.
    static FIELD: OnceLock<regex::Regex> = OnceLock::new();
    let field = FIELD.get_or_init(|| regex::Regex::new(r"profiles\.(?:[^ :;]+\.)?runtimes\.[^ .:;]+\.(account|five_hour)(?:[.: ;]|$)").unwrap());
    if let Some(key) = field.captures(detail).filter(|_| extra).map(|c| c[1].to_string()) {
        if has_in_profiles(body, &key) {
            return Some(format!("profiles.{key}"));
        }
    }
    if extra && detail.contains("weekly") && has_in_profiles(body, "weekly") {
        return Some("profiles.weekly".into());
    }
    if extra && detail.contains("record_updated") && rows(body, "worktrees").any(|r| r.get("record_updated").is_some()) {
        return Some("worktrees.record_updated".into()); // every worktree report still goes, unstamped
    }
    let caps: Vec<String> = rows(body, "capabilities").map(text_of).collect();
    if detail.contains("capabilities") {
        // Pydantic names the rejected element: preserve every other negotiated capability on rollback.
        if let Some(index) = regex::Regex::new(r"capabilities[.\[]([0-9]+)").ok()?.captures(detail)
            .and_then(|m| m[1].parse::<usize>().ok()) {
            if let Some(name) = caps.get(index) {
                if CAPABILITIES.contains(&name.as_str()) { return Some(name.clone()); }
            }
        }
        for name in ["memory_tidy_packets_v1", "browser_exit_v1", "private_lanes_v1", "turns_at_once_v1", "capacity_continue_v1", "source_continue_v1",
                     crate::login::SWITCH_TO_KEY_CAPABILITY, crate::login::REMOVE_CAPABILITY,
                     crate::login::SWITCH_TYPE_CAPABILITY, crate::login::KEYS_CAPABILITY,
                     crate::login::SIGN_OUT_CAPABILITY, crate::screen::CAPABILITY, "task_goals_v1",
                     "session_roots_v1", "task_lanes_v2"] {
            if caps.iter().any(|c| c == name) {
                return Some(name.into());
            }
        }
    }
    let present = ROOT_FIELDS.iter().filter(|f| body.get(**f).is_some());
    for field in present.clone() {
        if regex::Regex::new(&format!(r"(?:^|[ ;])(?:body\.)?{field}(?:[.: ;]|$)")).ok()?.is_match(detail) {
            return Some(field.to_string());
        }
    }
    static PATH: OnceLock<regex::Regex> = OnceLock::new();
    if extra && !PATH.get_or_init(|| regex::Regex::new(r"body\.|readiness\.").unwrap()).is_match(detail) {
        return present.clone().next().map(|f| f.to_string());
    }
    None
}

/// Take what a server refused in the last ten minutes out of a heartbeat body about to be sent.
pub fn drop_held(body: &mut Value) {
    if let Some(caps) = body["capabilities"].as_array_mut() {
        caps.retain(|c| !held_back(&text_of(c)));
    }
    for (hold, list, path) in [("profiles.weekly", "profiles", "runtimes"), ("profiles.account", "profiles", "runtimes"), ("profiles.five_hour", "profiles", "runtimes"), ("worktrees.record_updated", "worktrees", "")] {
        if !held_back(hold) {
            continue;
        }
        let key = hold.split('.').nth(1).unwrap();
        for row in body[list].as_array_mut().into_iter().flatten() {
            let targets: Vec<&mut Value> = if path.is_empty() { vec![row] } else { row[path].as_object_mut().into_iter().flat_map(|m| m.values_mut()).collect() };
            for t in targets {
                if let Some(m) = t.as_object_mut() {
                    m.remove(key);
                }
            }
        }
    }
    if let Some(b) = body.as_object_mut() {
        for field in ROOT_FIELDS {
            if held_back(field) {
                b.remove(*field);
            }
        }
    }
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
        // A server from before private lanes or turn limits refuses the newest capability first, even without naming
        // its position.
        let newest = json!({"capabilities": ["task_lanes_v2", "turns_at_once_v1", "private_lanes_v1"]});
        assert_eq!(refused_field("body.capabilities: bad", &newest).as_deref(), Some("private_lanes_v1"));
        let turns = json!({"capabilities": ["task_lanes_v2", "turns_at_once_v1"]});
        assert_eq!(refused_field("body.capabilities: bad", &turns).as_deref(), Some("turns_at_once_v1"));
        // A server from before model keys refuses only that capability; the sign-out one stays.
        let both = json!({"capabilities": ["task_lanes_v2", crate::login::SIGN_OUT_CAPABILITY, crate::login::KEYS_CAPABILITY]});
        assert!(CAPABILITIES.contains(&crate::login::KEYS_CAPABILITY));
        assert_eq!(refused_field("body.capabilities.2: Input should be 'task_lanes_v2'", &both).as_deref(), Some(crate::login::KEYS_CAPABILITY));
        // A server from before capacity continuation refuses that capability first, not an older one it accepts.
        let all = json!({"capabilities": CAPABILITIES});
        assert_eq!(refused_field("body.capabilities.3: Input should be 'task_lanes_v2'", &all).as_deref(), Some("capacity_continue_v1"));
        assert_eq!(refused_field("body.capabilities.4: Input should be 'capacity_continue_v1'", &all).as_deref(), Some("source_continue_v1"));
    }

    #[test]
    fn an_older_server_refuses_packets_without_losing_ordinary_work() {
        let mut body = json!({"capabilities": ["task_lanes_v2", "memory_tidy_packets_v1"]});
        assert_eq!(refused_field("body.capabilities.1: Input should be 'task_lanes_v2'", &body).as_deref(), Some("memory_tidy_packets_v1"));
        assert_eq!(refused_field("body.capabilities: bad", &body).as_deref(), Some("memory_tidy_packets_v1"));
        hold_back("memory_tidy_packets_v1");
        drop_held(&mut body);
        release("memory_tidy_packets_v1");
        assert_eq!(body["capabilities"], json!(["task_lanes_v2"]));
    }

    #[test]
    fn an_older_server_loses_only_the_field_it_refuses() {
        let mut body = json!({"profiles": [{"runtimes": {"codex": {"ok": true, "weekly": {"used_percent": 3}}}}],
                              "worktrees": [{"link_id": "l", "record_updated": "t"}], "repositories": [], "readiness": {}});
        let weekly = "body.profiles.0.runtimes.codex.weekly: Extra inputs are not permitted";
        assert_eq!(refused_field(weekly, &body).as_deref(), Some("profiles.weekly"));
        assert_eq!(refused_field("body.worktrees.0.record_updated: Extra inputs", &body).as_deref(), Some("worktrees.record_updated"));
        assert_eq!(refused_field("body.repositories: Extra inputs are not permitted", &body).as_deref(), Some("repositories"));
        assert_eq!(refused_field("extra fields not permitted", &body).as_deref(), Some("worktrees"));
        assert_eq!(refused_field("body.readiness.worktrees: Extra inputs", &body), None);
        hold_back("profiles.weekly");
        hold_back("repositories");
        drop_held(&mut body);
        assert_eq!(body["profiles"][0]["runtimes"]["codex"], json!({"ok": true}));
        assert!(body.get("repositories").is_none() && body["worktrees"][0].get("record_updated").is_some());
        dropped().lock().unwrap().retain(|k, _| k != "profiles.weekly" && k != "repositories");
    }

    #[test]
    fn an_older_server_loses_only_the_account_and_five_hour_window() {
        let mut body = json!({"profiles": [{"name": "acme", "runtimes": {"codex": {"signed_in": true, "weekly": {"used_percent": 3},
                              "account": {"email": "dev@acme.example", "plan": "pro"}, "five_hour": {"used_percent": 9}}}}], "readiness": {}});
        let account = "body.profiles.list[ComputerProfile].0.runtimes.codex.account: Extra inputs are not permitted";
        assert_eq!(refused_field(account, &body).as_deref(), Some("profiles.account"));
        let window = "body.profiles.0.runtimes.codex.five_hour: Extra inputs are not permitted";
        assert_eq!(refused_field(window, &body).as_deref(), Some("profiles.five_hour"));
        hold_back("profiles.account");
        hold_back("profiles.five_hour");
        drop_held(&mut body);
        assert_eq!(body["profiles"][0]["runtimes"]["codex"], json!({"signed_in": true, "weekly": {"used_percent": 3}}));
        dropped().lock().unwrap().retain(|k, _| k != "profiles.account" && k != "profiles.five_hour");
    }
}
