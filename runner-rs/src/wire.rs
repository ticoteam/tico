//! The runner wire contract (`contracts/runner/wire.json`, docs/runner-wire-contract.md) as the runner reads it:
//! the answers on the core turn path, each with only the fields the runner acts on. Fields it does not know are
//! ignored, so a newer server may add them. A field the contract requires is a plain field; one the runner has always
//! done without when absent (a server feature signal, a default an older server relied on) stays an `Option` with the
//! same fallback at its reader. The Python contract test holds the server to the whole contract; the test below reads
//! the same recorded answers, today's and each kept release's, with these types.

use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{Map, Value};

use crate::client::ApiError;

/// An answer as `T`; one the runner cannot read is a protocol error, like a body that is not JSON.
pub fn decode<T: DeserializeOwned>(answer: Value) -> Result<T, ApiError> {
    serde_json::from_value(answer).map_err(|e| {
        let detail = format!("Tico returned an answer this runner cannot read ({e}); check the server release");
        ApiError::new("protocol", detail, 0, false)
    })
}

/// `POST runners/heartbeat`. The booleans say the server takes or wants something; absent is a server from before it.
#[derive(Debug, Deserialize)]
pub struct HeartbeatAnswer {
    pub start_places: Option<bool>,
    pub runtime_credential_source: Option<bool>,
    pub subscription_accounts: Option<bool>,
    pub worktree_record_stamp: Option<bool>,
    pub worktree_actions: Option<Vec<Value>>,
    pub worktree_actions_skipped: Option<u64>,
    pub performance: Option<bool>,
    /// Absent or null is automatic: the machine's own capacity is the limit.
    pub turns_at_once: Option<u64>,
    pub restart: Option<bool>,
    /// True while this computer is an exit for bots (backend/exits.py): keep the exit link open (crate::exit_link).
    pub exit: Option<bool>,
}

/// `POST jobs/claim`. The attempt stays JSON: the turn reads it whole (execute.rs, state.rs).
#[derive(Debug, Deserialize)]
pub struct ClaimAnswer {
    pub attempt: Option<Map<String, Value>>,
    /// Why this computer gets no work (backend/runner_versions.py `paused`).
    pub paused: Option<String>,
}

/// `POST attempts/{aid}/start-place`.
#[derive(Debug, Deserialize)]
pub struct StartPlaceAnswer {
    pub reserved: bool,
    pub stop: Option<bool>,
}

/// `POST attempts/{aid}/events`.
#[derive(Debug, Deserialize)]
pub struct EventsAnswer {
    pub ack_seq: i64,
}

/// `POST attempts/{aid}/renew`.
#[derive(Debug, Deserialize)]
pub struct RenewAnswer {
    /// The runner renews ten seconds before this runs out; 60 when a server leaves it out.
    pub lease_seconds: Option<f64>,
    pub stop: Option<bool>,
    pub stop_reason: Option<String>,
}

/// The `ready` frame of `GET runners/me/events`.
#[derive(Debug, Default, Deserialize)]
pub struct ReadyFrame {
    pub seq: Option<i64>,
    /// The release runners should run; empty on a development server.
    pub release: Option<String>,
}

/// A `runner` frame: what changed for this computer. The kind is an open set: a kind this runner does not know asks
/// for everything again (events.rs `channels`), so it stays a string.
#[derive(Debug, Default, Deserialize)]
pub struct RunnerFrame {
    pub kind: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    /// Every recorded exchange: today's fixtures and each kept release's.
    fn fixtures() -> Vec<(PathBuf, Value)> {
        let contract = Path::new(env!("CARGO_MANIFEST_DIR")).join("../contracts/runner");
        let mut dirs = vec![contract.join("fixtures")];
        for release in std::fs::read_dir(contract.join("releases")).expect("contracts/runner/releases") {
            dirs.push(release.unwrap().path().join("fixtures"));
        }
        let mut found = vec![];
        for dir in dirs {
            for file in std::fs::read_dir(&dir).unwrap_or_else(|e| panic!("{}: {e}", dir.display())) {
                let path = file.unwrap().path();
                if path.extension().is_some_and(|x| x == "json") {
                    let text = std::fs::read_to_string(&path).unwrap();
                    found.push((path, serde_json::from_str(&text).unwrap()));
                }
            }
        }
        assert!(found.len() >= 20, "the fixtures are missing: {found:?}");
        found
    }

    fn read<T: DeserializeOwned>(path: &Path, answer: &Value) {
        if let Err(e) = decode::<T>(answer.clone()) {
            panic!("{}: {}", path.display(), e.detail);
        }
    }

    #[test]
    fn this_runner_reads_every_answer_the_server_recorded_today_and_in_each_kept_release() {
        for (path, fixture) in fixtures() {
            let answer = &fixture["answer"];
            let status = fixture["status"].as_u64().unwrap() as u16;
            if status != 200 {
                // A refusal reads as the error the runner acts on: its code, detail and retryability.
                let error = crate::client::error_from(status, answer.to_string().as_bytes(), "key");
                assert_eq!(error.code, answer["error"]["code"].as_str().unwrap(), "{}", path.display());
                assert_eq!(error.detail, answer["error"]["detail"].as_str().unwrap(), "{}", path.display());
                assert_eq!(error.retryable, status == 429 || status >= 500, "{}", path.display());
                continue;
            }
            match fixture["endpoint"].as_str().unwrap() {
                "heartbeat" => read::<HeartbeatAnswer>(&path, answer),
                "claim" => read::<ClaimAnswer>(&path, answer),
                "start_place" => read::<StartPlaceAnswer>(&path, answer),
                "events" => read::<EventsAnswer>(&path, answer),
                "renew" => read::<RenewAnswer>(&path, answer),
                "runner_events" if answer.get("release").is_some() => read::<ReadyFrame>(&path, answer),
                "runner_events" => read::<RunnerFrame>(&path, answer),
                "started" | "complete" => {} // the runner does not read these answers
                other => panic!("{}: no reader for the endpoint {other}", path.display()),
            }
        }
    }

    #[test]
    fn the_recorded_answers_carry_what_the_runner_acts_on() {
        let get = |name: &str| {
            let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("../contracts/runner/fixtures/{name}.json"));
            serde_json::from_str::<Value>(&std::fs::read_to_string(&path).unwrap()).unwrap()["answer"].clone()
        };
        let beat: HeartbeatAnswer = decode(get("heartbeat.ok")).unwrap();
        assert_eq!((beat.start_places, beat.worktree_record_stamp), (Some(true), Some(true)));
        assert!(beat.worktree_actions.is_some());
        let claim: ClaimAnswer = decode(get("claim.attempt")).unwrap();
        let attempt = claim.attempt.unwrap();
        assert!(attempt["id"].is_string() && attempt["bot"].is_string() && attempt["token"].is_string());
        let paused: ClaimAnswer = decode(get("claim.paused")).unwrap();
        assert!(paused.attempt.is_none() && paused.paused.is_some_and(|why| !why.is_empty()));
        let renew: RenewAnswer = decode(get("renew.stop")).unwrap();
        assert!(renew.lease_seconds.is_some() && renew.stop == Some(true));
        let ready: ReadyFrame = decode(get("runner_events.ready")).unwrap();
        assert!(ready.seq.is_some() && ready.release.is_some());
        let work: RunnerFrame = decode(get("runner_events.work")).unwrap();
        assert_eq!(work.kind.as_deref(), Some("work"));
    }

    #[test]
    fn an_answer_of_the_wrong_shape_is_a_protocol_error_not_a_guess() {
        let e = decode::<EventsAnswer>(serde_json::json!({"ack_seq": "seven"})).unwrap_err();
        assert_eq!((e.code.as_str(), e.retryable), ("protocol", false));
        assert!(decode::<StartPlaceAnswer>(serde_json::json!({"stop": true})).is_err(), "reserved is required");
        let newer: RenewAnswer =
            decode(serde_json::json!({"lease_seconds": 90, "lease_until": "x", "spare": {"a": 1}})).unwrap();
        assert_eq!(newer.lease_seconds, Some(90.0), "a field the runner does not know is ignored");
    }
}
