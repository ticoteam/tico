//! The small server relays the run loop follows each tick (service.py `poll_logins`, the subscription refresh
//! poll, `poll_credential_imports`, `step_harnesses`): browser sign-ins, weekly-allowance reads, credential
//! imports, and harness installs and owner actions. Each runs on its own task so the claim loop never waits,
//! never two of the same at once, asked when the event stream says so and on its own backup timer.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use serde_json::Value;

use crate::client::ApiError;
use crate::events::BACKUP_POLL_S;
use crate::runner::{describe, Runner};
use crate::util::{log, text_of};

type Slot<T> = Arc<tokio::sync::Mutex<T>>;

#[derive(Default)]
struct HarnessRelay {
    relay: Option<crate::harness_tools::Relay>,
    default_runtime: String,
    default_at: Option<Instant>,
}

struct Relays {
    logins: Slot<crate::login::Logins>,
    refresh: Slot<crate::subscription_refresh::Refreshes>,
    imports: Slot<()>,
    harness: Slot<HarnessRelay>,
    polled: Mutex<HashMap<&'static str, Instant>>,
    errors: Mutex<HashMap<&'static str, String>>,
}

fn relays() -> &'static Relays {
    static R: OnceLock<Relays> = OnceLock::new();
    R.get_or_init(|| Relays {
        logins: Default::default(),
        refresh: Default::default(),
        imports: Default::default(),
        harness: Default::default(),
        polled: Mutex::new(HashMap::new()),
        errors: Mutex::new(HashMap::new()),
    })
}

/// service.py `wants`: the stream asked for `channel`, it never ran, or its interval passed (`every` while the
/// stream is down, BACKUP_POLL_S while it is up).
fn wants(runner: &Runner, channel: &'static str, every: f64) -> bool {
    let now = Instant::now();
    let mut polled = relays().polled.lock().unwrap();
    let due = runner.events.take(channel)
        || polled.get(channel).is_none_or(|at| now.duration_since(*at).as_secs_f64() >= if runner.events.live() { BACKUP_POLL_S } else { every });
    if due {
        polled.insert(channel, now);
    }
    due
}

/// A relay failure is logged once per distinct error, never a runner outage.
fn note(what: &'static str, result: Result<(), ApiError>, says: &str) {
    let mut errors = relays().errors.lock().unwrap();
    match result {
        Ok(()) => {
            errors.remove(what);
        }
        Err(e) => {
            let text = describe(&e);
            if errors.get(what) != Some(&text) {
                log(&format!("Tico runner: {says} failed ({text}); will retry"));
                errors.insert(what, text);
            }
        }
    }
}

/// One tick's relays. Called from `Runner::tick` after the heartbeat; returns at once.
pub fn poll(runner: &Arc<Runner>) {
    if runner.stopping() {
        if let Ok(l) = relays().logins.try_lock() {
            l.stop();
        }
        if let Ok(r) = relays().refresh.try_lock() {
            r.stop();
        }
        return;
    }
    let r = relays();
    if let Ok(mut logins) = r.logins.clone().try_lock_owned() {
        let me = runner.clone();
        tokio::spawn(async move {
            // One under way is followed on its own timer (its terminal moves); otherwise asked when the server says.
            let result = if logins.busy() {
                logins.poll(&me, false).await
            } else if wants(&me, "logins", crate::login::POLL_S) {
                logins.poll(&me, true).await
            } else {
                Ok(())
            };
            note("logins", result, "sign-in poll");
        });
    }
    if let Ok(mut refresh) = r.refresh.clone().try_lock_owned() {
        let me = runner.clone();
        tokio::spawn(async move {
            // Optional on older servers: a failure is dropped, as service.py does.
            let ask = if refresh.busy() { None } else { Some(wants(&me, "refresh", crate::subscription_refresh::POLL_SECONDS)) };
            if ask != Some(false) {
                let _ = refresh.poll(&me, ask).await;
            }
        });
    }
    if let Ok(held) = r.imports.clone().try_lock_owned() {
        if wants(runner, "imports", crate::credential_imports::POLL_S) {
            let me = runner.clone();
            tokio::spawn(async move {
                let _held = held;
                note("imports", crate::credential_imports::poll(&me).await, "credential import poll");
            });
        }
    }
    if let Ok(mut harness) = r.harness.clone().try_lock_owned() {
        let me = runner.clone();
        tokio::spawn(async move {
            let tools = crate::harness_tools::init(&me.config);
            let result = async {
                // Install on demand: what this computer's bots run on (primary and fallback), plus what the owner
                // turned on here; a computer with no bots yet takes the company's default runtime.
                if harness.default_at.is_none_or(|t| t.elapsed().as_secs_f64() >= 60.0) {
                    harness.default_at = Some(Instant::now());
                    if let Ok(c) = me.client.get("config").await {
                        if let Some(value) = c["default_runtime"].as_str() {
                            harness.default_runtime = value.to_string();
                        }
                    }
                    if let Ok(rows) = me.client.get("runners/assignments").await {
                        let rows = rows.as_array().cloned().unwrap_or_default();
                        let runtimes: Vec<String> = harness_runtimes(&rows).into_iter().collect();
                        let default: Vec<&str> =
                            if runtimes.is_empty() && !harness.default_runtime.is_empty() { vec![&harness.default_runtime] } else { vec![] };
                        tools.lock().unwrap().want(&runtimes, &default);
                    }
                }
                let active = me.active.lock().unwrap().clone();
                let busy = crate::harness_tools::busy(&active);
                let ask = wants(&me, "harness", crate::harness_tools::POLL_S);
                harness.relay.get_or_insert_with(crate::harness_tools::Relay::new).tick(&tools, &me.client, busy, ask).await
            }
            .await;
            note("harness", result, "harness step");
        });
    }
}

/// The runtimes this computer's bots run on (service.harness_runtimes): each assigned bot's and its fallback's.
pub fn harness_runtimes(rows: &[Value]) -> HashSet<String> {
    let mut found = HashSet::new();
    for row in rows {
        found.insert(text_of(&row["config"]["runtime"]));
        if let Some(hop) = crate::fallback::configured(&row["config"]) {
            found.insert(hop.runtime);
        }
    }
    found.remove("");
    found
}
