//! Task lanes (task_lanes_v2): a task-keyed turn runs in its own lane beside the bot's other work, one turn at
//! a time per (bot, lane) on this computer (sessions::Lanes). The claim names
//! the lanes busy here (`busy_lanes`); the heartbeat advertises the capabilities, each dropped for ten minutes
//! by a server that refuses it.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::util::text_of;

/// The runner starts a new thread on a fork of the session the claim names (`fork_thread`, execute.rs).
pub const FORK_SESSION: &str = "fork_session_v1";
/// The runner starts a claim marked `fresh_session` on a new thread and keeps the conversation's saved one (execute.rs).
pub const FRESH_SESSION: &str = "fresh_session_v1";
/// A Claude thread is keyed by no one plan, so a move between plans resumes it (model_subscriptions::Delivered::binding);
/// without it the server starts a computer's Claude sessions fresh on each move.
pub const PLAN_SWITCH_RESUME: &str = "plan_switch_resume_v1";

const VOICE_SESSIONS: &str = "voice_sessions_v1";
const VOICE_SESSION_RESUME: &str = "voice_session_resume_v1";
/// The server pushes `voice_inputs` hints to a resident voice session (events.rs VoiceInputs); without them it polls.
pub const VOICE_INPUTS: &str = "voice_inputs_v1";
/// The server tells this computer when its meeting importers or the config its harness relay reads change, so those
/// reads wait for the stream's word and a 5-minute backstop instead of a minute's clock (events.rs relays_pushed).
pub const RELAY_HINTS: &str = "relay_hints_v1";
/// Reads a Claude plan's week on request (subscription_refresh.rs `read_claude_plan`; backend/subscription_refresh.py
/// `request_plan_read`).
const PLAN_USAGE: &str = "plan_usage_v1";
/// The computer's own logins are the ones readiness reads for these bots (runner.rs `computer_logins_for`).
const COMPUTER_LOGINS: &str = "computer_logins_v1";
pub const CAPABILITIES: &[&str] = &[
    "task_lanes_v2",
    "session_roots_v1",
    "task_goals_v1",
    "capacity_continue_v1",
    "source_continue_v1",
    crate::login::SIGN_OUT_CAPABILITY,
    crate::login::KEYS_CAPABILITY,
    crate::login::SWITCH_TYPE_CAPABILITY,
    crate::login::REMOVE_CAPABILITY,
    crate::login::SWITCH_TO_KEY_CAPABILITY,
    crate::login::CONNECT_CAPABILITY,
    // Full Claude logins: a turn's own credentials file, refreshes and usage reads (claude_login.rs).
    crate::claude_login::CAPABILITY,
    "turns_at_once_v1",
    // A previous hub release can still send the retired meeting lane (sessions.rs).
    "private_lanes_v1",
    "model_subscriptions_v1",
    "model_subscription_activation_v1",
    // Turns of a bot with a browser exit go through it (browser_exit.rs); the server hands them to no other runner.
    "browser_exit_v1",
    // Chats of a bot keep their own provider threads: a public thread never resumes a direct message's (execute.rs session_key).
    "threads_v1",
    // Report the process allocation boundary so the hub can recheck memory on unused grants.
    "harness_allocation_v1",
    // Make page uses a fresh provider session in disposable scratch, apart from the private Assistant's session.
    "make_page",
    FORK_SESSION,
    FRESH_SESSION,
    PLAN_SWITCH_RESUME,
    VOICE_SESSIONS,
    // A push to a bot's repository is heard at once, so its chat turns never start on an older fetch (fetch_fresh.rs).
    crate::fetch_fresh::PUSH_HINTS,
    VOICE_SESSION_RESUME,
    VOICE_INPUTS,
    RELAY_HINTS,
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
    running()
        .lock()
        .unwrap()
        .insert(text_of(&attempt["id"]), row);
}

pub fn forget(aid: &str) {
    running().lock().unwrap().remove(aid);
}

#[allow(dead_code)] // the harness installer's mid-turn guard reads it
pub fn runtimes_in_use() -> BTreeSet<String> {
    running()
        .lock()
        .unwrap()
        .values()
        .flat_map(|r| r.runtimes.iter().cloned())
        .collect()
}

fn dropped() -> &'static Mutex<HashMap<String, Instant>> {
    static D: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();
    D.get_or_init(Default::default)
}

/// Whether a heartbeat or claim field is held back from a server that refused it in the last ten minutes.
pub fn held_back(name: &str) -> bool {
    dropped()
        .lock()
        .unwrap()
        .get(name)
        .is_some_and(|at| at.elapsed() < DROP_FOR)
}

pub fn hold_back(name: &str) {
    dropped()
        .lock()
        .unwrap()
        .insert(name.into(), Instant::now());
}

#[cfg(test)]
pub fn release(name: &str) {
    dropped().lock().unwrap().remove(name);
}

/// Capability holds are process-wide: tests that set them take turns, so one never drops another's.
#[cfg(test)]
pub fn holding() -> std::sync::MutexGuard<'static, ()> {
    static TURN: Mutex<()> = Mutex::new(());
    TURN.lock().unwrap_or_else(|e| e.into_inner())
}

/// Every capability a heartbeat may carry, newest first: what a refusal that names no element drops, one by one, so
/// an older server loses only what it does not know. A new capability goes at the top (`newest_first_lists_all`).
const NEWEST_FIRST: &[&str] = &[
    RELAY_HINTS,
    VOICE_INPUTS,
    VOICE_SESSION_RESUME,
    crate::fetch_fresh::PUSH_HINTS,
    VOICE_SESSIONS,
    PLAN_SWITCH_RESUME,
    crate::claude_login::CAPABILITY,
    PLAN_USAGE,
    FRESH_SESSION,
    FORK_SESSION,
    crate::browser::CAPABILITY,
    "make_page",
    "harness_allocation_v1",
    "threads_v1",
    "model_subscription_activation_v1",
    "browser_exit_v1",
    "model_subscriptions_v1",
    "private_lanes_v1",
    "turns_at_once_v1",
    "capacity_continue_v1",
    "source_continue_v1",
    crate::login::CONNECT_CAPABILITY,
    crate::login::SWITCH_TO_KEY_CAPABILITY,
    crate::login::REMOVE_CAPABILITY,
    crate::login::SWITCH_TYPE_CAPABILITY,
    crate::login::KEYS_CAPABILITY,
    crate::login::SIGN_OUT_CAPABILITY,
    COMPUTER_LOGINS,
    crate::screen::CAPABILITY,
    "task_goals_v1",
    "session_roots_v1",
    "task_lanes_v2",
];

/// The capabilities this heartbeat advertises, less any a server refused in the last ten minutes. `computer_logins`:
/// the computer's own logins are the ones readiness reads for its bots (runner.rs `computer_logins_for`).
pub fn capabilities(computer_logins: bool) -> Vec<&'static str> {
    let screen = crate::screen::offers(crate::screen::readiness().as_ref())
        .then_some(crate::screen::CAPABILITY);
    // Website tools' browser sessions run here only while Google Chrome can show a window (crate::browser).
    let browser = crate::browser::offers().then_some(crate::browser::CAPABILITY);
    CAPABILITIES
        .iter()
        .copied()
        .chain(screen)
        .chain(browser)
        .chain([PLAN_USAGE])
        .chain(computer_logins.then_some(COMPUTER_LOGINS))
        .filter(|c| !held_back(c))
        .collect()
}

/// Root-level heartbeat fields an older server may refuse; each is left out for ten minutes after a refusal.
const ROOT_FIELDS: &[&str] = &[
    "worktrees",
    "profiles",
    "repositories",
    "restart",
    "device_id",
];

fn rows<'a>(body: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    body[key].as_array().into_iter().flatten()
}

fn has_in_profiles(body: &Value, key: &str) -> bool {
    rows(body, "profiles").any(|p| {
        p["runtimes"]
            .as_object()
            .is_some_and(|r| r.values().any(|s| s.get(key).is_some()))
    })
}

/// What to leave out after a heartbeat's 422 (service.py _report_heartbeat), as a hold name: only `weekly` of the
/// profile report, the worktree reports' `record_updated` stamp, the capability it names by position (any of them; the
/// newest sent when it names none), else a root field
/// the detail names (or, for a bare "extra" with no path, the first one present). None: the refusal stands.
pub fn refused_field(detail: &str, body: &Value) -> Option<String> {
    let extra = detail.to_lowercase().contains("extra");
    // Older servers keep receiving the sign-in report without the field they refuse.
    static FIELD: OnceLock<regex::Regex> = OnceLock::new();
    let field = FIELD.get_or_init(|| {
        regex::Regex::new(
            r"profiles\.(?:[^ :;]+\.)?runtimes\.[^ .:;]+\.(account|five_hour)(?:[.: ;]|$)",
        )
        .unwrap()
    });
    if let Some(key) = field
        .captures(detail)
        .filter(|_| extra)
        .map(|c| c[1].to_string())
    {
        if has_in_profiles(body, &key) {
            return Some(format!("profiles.{key}"));
        }
    }
    if extra && detail.contains("weekly") && has_in_profiles(body, "weekly") {
        return Some("profiles.weekly".into());
    }
    if extra
        && detail.contains("record_updated")
        && rows(body, "worktrees").any(|r| r.get("record_updated").is_some())
    {
        return Some("worktrees.record_updated".into()); // every worktree report still goes, unstamped
    }
    let caps: Vec<String> = rows(body, "capabilities").map(text_of).collect();
    // Only an error at the top-level `capabilities` or one of its elements: a bot or field named "capabilities"
    // further down (say readiness.bots.capabilities) is not about them.
    static CAPS: OnceLock<regex::Regex> = OnceLock::new();
    let refused = CAPS
        .get_or_init(|| {
            regex::Regex::new(r"(?:^|; )(?:body\.)?capabilities(?:[.\[]([0-9]+)\]?)?:").unwrap()
        })
        .captures(detail);
    if let Some(refused) = refused {
        // Pydantic names the rejected element: drop that one, whatever it is, and keep every other negotiated one.
        let index = refused
            .get(1)
            .and_then(|i| i.as_str().parse::<usize>().ok());
        if let Some(name) = index.and_then(|i| caps.get(i)).filter(|n| !n.is_empty()) {
            return Some(name.clone());
        }
        if let Some(name) = NEWEST_FIRST.iter().find(|n| caps.iter().any(|c| c == *n)) {
            return Some(name.to_string());
        }
    }
    let present = ROOT_FIELDS.iter().filter(|f| body.get(**f).is_some());
    for field in present.clone() {
        if regex::Regex::new(&format!(r"(?:^|[ ;])(?:body\.)?{field}(?:[.: ;]|$)"))
            .ok()?
            .is_match(detail)
        {
            return Some(field.to_string());
        }
    }
    static PATH: OnceLock<regex::Regex> = OnceLock::new();
    if extra
        && !PATH
            .get_or_init(|| regex::Regex::new(r"body\.|readiness\.").unwrap())
            .is_match(detail)
    {
        return present.clone().next().map(|f| f.to_string());
    }
    None
}

/// Take what a server refused in the last ten minutes out of a heartbeat body about to be sent.
pub fn drop_held(body: &mut Value) {
    if let Some(caps) = body["capabilities"].as_array_mut() {
        caps.retain(|c| !held_back(&text_of(c)));
    }
    for (hold, list, path) in [
        ("profiles.weekly", "profiles", "runtimes"),
        ("profiles.account", "profiles", "runtimes"),
        ("profiles.five_hour", "profiles", "runtimes"),
        ("worktrees.record_updated", "worktrees", ""),
    ] {
        if !held_back(hold) {
            continue;
        }
        let key = hold.split('.').nth(1).unwrap();
        for row in body[list].as_array_mut().into_iter().flatten() {
            let targets: Vec<&mut Value> = if path.is_empty() {
                vec![row]
            } else {
                row[path]
                    .as_object_mut()
                    .into_iter()
                    .flat_map(|m| m.values_mut())
                    .collect()
            };
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
    use std::path::{Path, PathBuf};

    /// Everything a heartbeat can advertise, in the order `capabilities` builds it on a computer that offers its
    /// screen and a browser and reads its own logins.
    fn sendable() -> Vec<&'static str> {
        CAPABILITIES
            .iter()
            .copied()
            .chain([
                crate::screen::CAPABILITY,
                crate::browser::CAPABILITY,
                PLAN_USAGE,
                COMPUTER_LOGINS,
            ])
            .collect()
    }

    fn release_all() {
        for name in sendable() {
            release(name);
        }
    }

    fn contract() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../contracts/runner")
    }

    fn read(path: &Path) -> Value {
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    /// A server's Heartbeat `capabilities` (backend/models.py, as its requests.json has it): the values it takes and
    /// how many at most.
    fn takes(requests: &Path) -> (Vec<String>, usize) {
        let caps = &read(requests)["$defs"]["Heartbeat"]["properties"]["capabilities"];
        let known = caps["items"]["enum"].as_array().unwrap();
        let max = caps["maxItems"].as_u64().unwrap() as usize;
        (known.iter().map(text_of).collect(), max)
    }

    /// The current server, then every kept release (contracts/runner/releases/<version>, the supported floor 0.6.0).
    fn servers() -> Vec<(String, Vec<String>, usize)> {
        let mut found = vec![("current".to_string(), contract().join("requests.json"))];
        let mut kept: Vec<PathBuf> = std::fs::read_dir(contract().join("releases"))
            .unwrap()
            .map(|e| e.unwrap().path().join("requests.json"))
            .filter(|p| p.exists())
            .collect();
        kept.sort();
        assert!(
            !kept.is_empty(),
            "no kept release under contracts/runner/releases"
        );
        for path in kept {
            let version = path.parent().unwrap().file_name().unwrap();
            found.push((version.to_string_lossy().into(), path));
        }
        found
            .into_iter()
            .map(|(name, path)| {
                let (known, max) = takes(&path);
                (name, known, max)
            })
            .collect()
    }

    /// What that server's validation answers a heartbeat with (backend/app.py `validation_error`: each Pydantic
    /// error as `loc: msg`, joined by "; "), or None when it takes it. `indexed`: the refusal names each element.
    fn refusal(known: &[String], max: usize, body: &Value, indexed: bool) -> Option<String> {
        let caps: Vec<String> = rows(body, "capabilities").map(text_of).collect();
        if caps.len() > max {
            return Some(format!(
                "body.capabilities: List should have at most {max} items after validation, not {}",
                caps.len()
            ));
        }
        let quoted: Vec<String> = known.iter().map(|k| format!("'{k}'")).collect();
        let errors: Vec<String> = caps
            .iter()
            .enumerate()
            .filter(|(_, c)| !known.contains(c))
            .map(|(i, _)| match indexed {
                true => format!(
                    "body.capabilities.{i}: Input should be {}",
                    quoted.join(", ")
                ),
                false => "body.capabilities: Input should be a valid list".into(),
            })
            .collect();
        (!errors.is_empty()).then(|| errors.join("; "))
    }

    /// runner.rs `post_heartbeat` against that server: post, and on a 422 hold back what `refused_field` names and
    /// post again, at most 12 times. The capabilities it got through with, and how many posts it took.
    fn negotiate(known: &[String], max: usize, indexed: bool) -> (Vec<String>, u32) {
        let mut body = json!({"capabilities": sendable()});
        drop_held(&mut body);
        let mut tries = 0;
        loop {
            tries += 1;
            let Some(detail) = refusal(known, max, &body, indexed) else {
                return (rows(&body, "capabilities").map(text_of).collect(), tries);
            };
            assert!(tries < 12, "still refused after {tries} posts: {detail}");
            let field = refused_field(&detail, &body)
                .unwrap_or_else(|| panic!("the runner gives up on: {detail}"));
            hold_back(&field);
            drop_held(&mut body);
        }
    }

    #[test]
    fn newest_first_lists_every_capability_the_runner_sends() {
        let mut all = sendable();
        all.sort();
        let mut listed = NEWEST_FIRST.to_vec();
        listed.sort();
        listed.dedup();
        assert_eq!(
            listed.len(),
            NEWEST_FIRST.len(),
            "a capability is listed twice"
        );
        assert_eq!(listed, all);
        let _turn = holding();
        release_all();
        // What runner.rs advertises comes only from here, plan usage and computer logins included.
        let advertised = capabilities(true);
        assert!(advertised.iter().all(|c| all.contains(c)));
        assert!(advertised.contains(&PLAN_USAGE) && advertised.contains(&COMPUTER_LOGINS));
        assert!(!capabilities(false).contains(&COMPUTER_LOGINS));
    }

    /// The hubs between the floor and today, by what each added to the floor's Heartbeat capabilities (git tags).
    fn releases_since_floor(floor: &[String]) -> Vec<(String, Vec<String>)> {
        let mut known = floor.to_vec();
        let mut found = vec![];
        for (release, added) in [
            ("0.6.0-rc.23", &[crate::browser::CAPABILITY][..]),
            ("0.6.0-rc.30", &[FORK_SESSION, FRESH_SESSION][..]),
            ("0.6.16", &[PLAN_USAGE][..]),
            (
                "0.6.37",
                &[crate::claude_login::CAPABILITY, PLAN_SWITCH_RESUME][..],
            ),
            ("0.6.71", &[VOICE_SESSIONS][..]),
            ("0.6.76", &[crate::fetch_fresh::PUSH_HINTS][..]),
            // Merged after 0.6.79 and before voice_inputs_v1, which only today's hub (current) knows.
            ("0.6.79+", &[VOICE_SESSION_RESUME][..]),
        ] {
            known.extend(added.iter().map(|c| c.to_string()));
            found.push((release.to_string(), known.clone()));
        }
        found
    }

    #[test]
    fn a_new_runner_checks_in_with_every_supported_server_and_keeps_what_it_knows() {
        let _turn = holding();
        let mut servers = servers();
        let floor = servers[1].1.clone(); // the oldest kept release: the supported floor
        for (release, known) in releases_since_floor(&floor) {
            servers.push((release, known, 32));
        }
        for (server, known, max) in servers {
            assert!(sendable().len() <= max, "{server} takes at most {max}");
            for indexed in [true, false] {
                release_all();
                let (kept, tries) = negotiate(&known, max, indexed);
                let expected: Vec<String> = sendable()
                    .into_iter()
                    .filter(|c| known.iter().any(|k| k == c))
                    .map(String::from)
                    .collect();
                assert_eq!(kept, expected, "{server} (indexed: {indexed})");
                let unknown = sendable().len() - expected.len();
                assert_eq!(tries as usize, unknown + 1, "{server} (indexed: {indexed})");
                if server == "current" {
                    assert_eq!(
                        tries, 1,
                        "today's server takes everything today's runner sends"
                    );
                }
                // The next heartbeat inside ten minutes goes straight through: what runner.rs advertises already
                // leaves out every hold, plan usage and computer logins included.
                let next = json!({"capabilities": capabilities(true)});
                assert_eq!(refusal(&known, max, &next, true), None, "{server}");
            }
        }
        // A server that takes fewer than the runner offers: the newest go first, until the rest fit.
        release_all();
        let current = takes(&contract().join("requests.json")).0;
        let (kept, tries) = negotiate(&current, sendable().len() - 2, true);
        let expected: Vec<String> = sendable()
            .into_iter()
            .filter(|c| !NEWEST_FIRST[..2].contains(c))
            .map(String::from)
            .collect();
        assert_eq!((kept, tries), (expected, 3));
        release_all();
    }

    #[test]
    fn only_a_refusal_of_the_capabilities_themselves_drops_one() {
        let body = json!({"capabilities": ["task_lanes_v2", "threads_v1"], "readiness": {}});
        // A bot named "capabilities" is not the capability list.
        let bot =
            "body.readiness.StructuredReadiness.bots.capabilities.commands.0.name: Field required";
        assert_eq!(refused_field(bot, &body), None);
        let both = format!("{bot}; body.capabilities.1: Input should be 'task_lanes_v2'");
        assert_eq!(refused_field(&both, &body).as_deref(), Some("threads_v1"));
        assert_eq!(
            refused_field("capabilities.0: Input should be 'x'", &body).as_deref(),
            Some("task_lanes_v2")
        );
    }

    #[test]
    fn every_capability_an_older_server_refuses_is_gated_in_the_contract_both_ways() {
        let wire = read(&contract().join("wire.json"));
        let gated = wire["gated"].as_object().unwrap();
        let servers = servers();
        let (_, current, _) = &servers[0];
        for (server, known, _) in &servers[1..] {
            // New runner, old server: each capability that server refuses is one the runner drops (above), and the
            // contract says so, so scripts/check_wire_contract.py does not wave a new one through unlisted.
            for name in sendable() {
                if !known.iter().any(|k| k == name) {
                    let key = format!("heartbeat.request.capabilities[]=\"{name}\"");
                    assert!(gated.contains_key(&key), "{server}: {key} is not gated");
                }
            }
            // Old runner, new server: today's server takes every capability that release's runner may send.
            for name in known {
                assert!(current.contains(name), "{server}'s {name} is refused today");
            }
        }
    }

    #[test]
    fn task_turns_get_their_own_lane() {
        let lane = crate::sessions::turn_lane;
        assert_eq!(
            lane(&json!({"bot": "ops", "config": {}, "task": {"id": "t1"}})),
            "task:t1"
        );
        assert_eq!(
            lane(&json!({"bot": "ops", "config": {}, "lane": "task:t9"})),
            "task:t9"
        );
        assert_eq!(
            lane(&json!({"bot": "ops", "config": {}, "conversation": {"id": "c"}})),
            "main"
        );
        track(
            &json!({"id": "a1", "bot": "ops", "config": {"runtime": "codex", "fallback": {"harness": "claude", "model": "m"}}, "task": {"id": "t1"}}),
        );
        track(&json!({"id": "a2", "bot": "ops", "config": {"runtime": "codex"}}));
        assert!(runtimes_in_use().contains("claude"));
        forget("a1");
        forget("a2");
        assert_eq!(
            refused_field(
                "body.capabilities: bad",
                &json!({"capabilities": ["task_lanes_v2", "task_goals_v1"]})
            )
            .as_deref(),
            Some("task_goals_v1")
        );
        // A server from before private lanes or turn limits refuses the newest capability first, even without naming
        // its position.
        let subscription =
            json!({"capabilities": ["model_subscriptions_v1", "model_subscription_activation_v1"]});
        assert_eq!(
            refused_field("body.capabilities: bad", &subscription).as_deref(),
            Some("model_subscription_activation_v1")
        );
        assert_eq!(
            refused_field(
                "body.capabilities.1: Input should be 'model_subscriptions_v1'",
                &subscription
            )
            .as_deref(),
            Some("model_subscription_activation_v1")
        );
        let newest =
            json!({"capabilities": ["task_lanes_v2", "turns_at_once_v1", "private_lanes_v1"]});
        assert_eq!(
            refused_field("body.capabilities: bad", &newest).as_deref(),
            Some("private_lanes_v1")
        );
        let turns = json!({"capabilities": ["task_lanes_v2", "turns_at_once_v1"]});
        assert_eq!(
            refused_field("body.capabilities: bad", &turns).as_deref(),
            Some("turns_at_once_v1")
        );
        // A server from before model keys refuses only that capability; the sign-out one stays.
        let both = json!({"capabilities": ["task_lanes_v2", crate::login::SIGN_OUT_CAPABILITY, crate::login::KEYS_CAPABILITY]});
        assert!(CAPABILITIES.contains(&crate::login::KEYS_CAPABILITY));
        assert_eq!(
            refused_field(
                "body.capabilities.2: Input should be 'task_lanes_v2'",
                &both
            )
            .as_deref(),
            Some(crate::login::KEYS_CAPABILITY)
        );
        // A server from before capacity continuation refuses that capability first, not an older one it accepts.
        let all = json!({"capabilities": CAPABILITIES});
        assert_eq!(
            refused_field("body.capabilities.3: Input should be 'task_lanes_v2'", &all).as_deref(),
            Some("capacity_continue_v1")
        );
        assert_eq!(
            refused_field(
                "body.capabilities.4: Input should be 'capacity_continue_v1'",
                &all
            )
            .as_deref(),
            Some("source_continue_v1")
        );
    }

    #[test]
    fn an_older_server_can_drop_make_page_without_losing_ordinary_work() {
        let _turn = holding();
        let mut body = json!({"capabilities": ["task_lanes_v2", "threads_v1", "make_page"]});
        assert!(CAPABILITIES.contains(&"make_page"));
        // A server from before website tools refuses the browser first, by position or not.
        let browser = json!({"capabilities": ["make_page", "browser_v1"]});
        assert_eq!(
            refused_field("body.capabilities: bad", &browser).as_deref(),
            Some("browser_v1")
        );
        assert_eq!(
            refused_field("body.capabilities.1: Input should be 'make_page'", &browser).as_deref(),
            Some("browser_v1")
        );
        assert_eq!(
            refused_field(
                "body.capabilities.2: Input should be 'task_lanes_v2'",
                &body
            )
            .as_deref(),
            Some("make_page")
        );
        assert_eq!(
            refused_field("body.capabilities: bad", &body).as_deref(),
            Some("make_page")
        );
        hold_back("make_page");
        drop_held(&mut body);
        release("make_page");
        assert_eq!(body["capabilities"], json!(["task_lanes_v2", "threads_v1"]));
    }

    #[test]
    fn the_runner_offers_to_fork_a_session_and_an_older_server_drops_only_that() {
        assert!(CAPABILITIES.contains(&FORK_SESSION));
        let all = json!({"capabilities": CAPABILITIES});
        let older: Vec<&str> = CAPABILITIES
            .iter()
            .copied()
            .filter(|c| {
                ![
                    RELAY_HINTS,
                    VOICE_INPUTS,
                    crate::fetch_fresh::PUSH_HINTS,
                    VOICE_SESSION_RESUME,
                    VOICE_SESSIONS,
                    FRESH_SESSION,
                    crate::claude_login::CAPABILITY,
                    PLAN_SWITCH_RESUME,
                ]
                .contains(c)
            })
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(FORK_SESSION)
        );
        let index = CAPABILITIES
            .iter()
            .position(|c| *c == FORK_SESSION)
            .unwrap();
        assert_eq!(
            refused_field(
                &format!("body.capabilities.{index}: Input should be 'task_lanes_v2'"),
                &all
            )
            .as_deref(),
            Some(FORK_SESSION)
        );
    }

    #[test]
    fn the_runner_offers_a_fresh_session_and_an_older_server_drops_only_that() {
        let _turn = holding();
        assert!(CAPABILITIES.contains(&FRESH_SESSION));
        let all = json!({"capabilities": CAPABILITIES});
        // A bare capabilities refusal from an older hub drops the newest offer first.
        assert_eq!(
            refused_field("body.capabilities: bad", &all).as_deref(),
            Some(RELAY_HINTS)
        );
        let older: Vec<&str> = CAPABILITIES
            .iter()
            .copied()
            .filter(|c| *c != RELAY_HINTS)
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(VOICE_INPUTS)
        );
        let older: Vec<&str> = older.into_iter().filter(|c| *c != VOICE_INPUTS).collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(VOICE_SESSION_RESUME)
        );
        let older: Vec<&str> = older
            .into_iter()
            .filter(|c| *c != VOICE_SESSION_RESUME)
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(crate::fetch_fresh::PUSH_HINTS)
        );
        let older: Vec<&str> = older
            .into_iter()
            .filter(|c| *c != crate::fetch_fresh::PUSH_HINTS)
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(VOICE_SESSIONS)
        );
        let older: Vec<&str> = older.into_iter().filter(|c| *c != VOICE_SESSIONS).collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(PLAN_SWITCH_RESUME)
        );
        let older: Vec<&str> = older
            .into_iter()
            .filter(|c| *c != PLAN_SWITCH_RESUME)
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(crate::claude_login::CAPABILITY)
        );
        let older: Vec<&str> = older
            .into_iter()
            .filter(|c| *c != crate::claude_login::CAPABILITY)
            .collect();
        assert_eq!(
            refused_field("body.capabilities: bad", &json!({"capabilities": older})).as_deref(),
            Some(FRESH_SESSION)
        );
        let index = CAPABILITIES
            .iter()
            .position(|c| *c == FRESH_SESSION)
            .unwrap();
        assert_eq!(
            refused_field(
                &format!("body.capabilities.{index}: Input should be 'task_lanes_v2'"),
                &all
            )
            .as_deref(),
            Some(FRESH_SESSION)
        );
        let mut body = all.clone();
        hold_back(FRESH_SESSION);
        drop_held(&mut body);
        release(FRESH_SESSION);
        let kept: Vec<&str> = CAPABILITIES
            .iter()
            .copied()
            .filter(|c| *c != FRESH_SESSION)
            .collect();
        assert_eq!(body["capabilities"], json!(kept));
    }

    #[test]
    fn an_older_server_loses_only_the_field_it_refuses() {
        let mut body = json!({"profiles": [{"runtimes": {"codex": {"ok": true, "weekly": {"used_percent": 3}}}}],
                              "worktrees": [{"link_id": "l", "record_updated": "t"}], "repositories": [], "readiness": {}});
        let weekly = "body.profiles.0.runtimes.codex.weekly: Extra inputs are not permitted";
        assert_eq!(
            refused_field(weekly, &body).as_deref(),
            Some("profiles.weekly")
        );
        assert_eq!(
            refused_field("body.worktrees.0.record_updated: Extra inputs", &body).as_deref(),
            Some("worktrees.record_updated")
        );
        assert_eq!(
            refused_field("body.repositories: Extra inputs are not permitted", &body).as_deref(),
            Some("repositories")
        );
        assert_eq!(
            refused_field("extra fields not permitted", &body).as_deref(),
            Some("worktrees")
        );
        assert_eq!(
            refused_field("body.readiness.worktrees: Extra inputs", &body),
            None
        );
        hold_back("profiles.weekly");
        hold_back("repositories");
        drop_held(&mut body);
        assert_eq!(
            body["profiles"][0]["runtimes"]["codex"],
            json!({"ok": true})
        );
        assert!(
            body.get("repositories").is_none()
                && body["worktrees"][0].get("record_updated").is_some()
        );
        dropped()
            .lock()
            .unwrap()
            .retain(|k, _| k != "profiles.weekly" && k != "repositories");
    }
}
