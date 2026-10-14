//! Read a subscription's weekly allowance through the signed-in CLI, without a thread, turn or paid request
//! (runner/subscription_refresh.py): `codex app-server`'s `account/rateLimits/read`, relayed for the server's
//! runner-subscription-refreshes. Only fixed states travel back, never a provider's error text.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::client::ApiError;
use crate::config::Config;
use crate::runner::Runner;
use crate::util::{iso, parse_iso, text_of};

pub const POLL_SECONDS: f64 = 15.0;
const TIMEOUT: Duration = Duration::from_secs(15);

fn now_iso_micros() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let base = text_of(&iso(&json!(now.as_secs())));
    // Python's datetime.now(timezone.utc).isoformat(): microseconds before the offset.
    base.replace("+00:00", &format!(".{:06}+00:00", now.subsec_micros()))
}

async fn rpc(
    stdin: &mut tokio::process::ChildStdin,
    lines: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    id: u64,
    method: &str,
    params: Value,
) -> Result<Value, ()> {
    let msg = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string() + "\n";
    stdin.write_all(msg.as_bytes()).await.map_err(|_| ())?;
    let read = async {
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
            if v.get("method").is_none() && v["id"] == json!(id) {
                return if v.get("error").is_some() { Err(()) } else { Ok(v["result"].clone()) };
            }
        }
        Err(())
    };
    tokio::time::timeout(TIMEOUT, read).await.map_err(|_| ())?
}

/// The weekly window from a `account/rateLimits/read` reply, as the report's fields.
pub fn weekly(reply: &Value, now: f64) -> Value {
    let buckets = &reply["rateLimitsByLimitId"];
    let bucket = if buckets.is_object() { &buckets["codex"] } else { &reply["rateLimits"] };
    if !bucket.is_object() || !(bucket["limitId"].is_null() || bucket["limitId"] == "codex") {
        return json!({"state": "unavailable"});
    }
    let windows: Vec<&Value> =
        [&bucket["primary"], &bucket["secondary"]].into_iter().filter(|w| w.is_object() && w["windowDurationMins"] == json!(10080)).collect();
    if windows.len() != 1 {
        return json!({"state": "unavailable"});
    }
    let window = windows[0];
    let percent = &window["usedPercent"];
    let reset = iso(&window["resetsAt"]);
    let p = percent.as_f64().filter(|p| percent.is_number() && p.is_finite() && (0.0..=100.0).contains(p));
    let Some(p) = p else { return json!({"state": "failed"}) };
    let left = if reset.is_null() { -1.0 } else { parse_iso(&text_of(&reset)) - now };
    if !(left > 0.0 && left <= 8.0 * 86400.0) {
        return json!({"state": "failed"});
    }
    json!({"state": "succeeded", "weekly": {"used_percent": percent, "resets_at": reset, "reported_at": now_iso_micros(),
           "status": if p >= 100.0 { json!("rejected") } else { Value::Null }}})
}

/// One read, with the profile's own Codex home and no API keys.
pub async fn read_codex(profile: &crate::profiles::Profile, cancelled: &AtomicBool) -> Value {
    let Some(exe) = crate::env::which("codex", &std::env::var("PATH").unwrap_or_default()) else { return json!({"state": "unavailable"}) };
    let process: crate::env::Env = std::env::vars().collect();
    let mut env = profile.environment("codex", &process);
    for name in ["OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"] {
        env.remove(name);
    }
    if cancelled.load(Ordering::SeqCst) {
        return json!({"state": "failed"});
    }
    let mut cmd = tokio::process::Command::new(exe);
    cmd.arg("app-server").env_clear().envs(&env).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).kill_on_drop(true);
    let Ok(mut child) = cmd.spawn() else { return json!({"state": "failed"}) };
    let (Some(mut stdin), Some(stdout)) = (child.stdin.take(), child.stdout.take()) else { return json!({"state": "failed"}) };
    let mut lines = BufReader::new(stdout).lines();
    let result = async {
        rpc(&mut stdin, &mut lines, 1, "initialize", json!({"clientInfo": {"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"}})).await?;
        let note = json!({"jsonrpc": "2.0", "method": "initialized", "params": {}}).to_string() + "\n";
        stdin.write_all(note.as_bytes()).await.map_err(|_| ())?;
        if cancelled.load(Ordering::SeqCst) {
            return Err(());
        }
        rpc(&mut stdin, &mut lines, 2, "account/rateLimits/read", json!({})).await
    }
    .await;
    drop(stdin);
    if tokio::time::timeout(Duration::from_secs(5), child.wait()).await.is_err() {
        let _ = child.kill().await;
    }
    match result {
        Ok(reply) => weekly(&reply, crate::util::epoch_s()),
        Err(()) => json!({"state": "failed"}),
    }
}

/// One bounded worker per computer; a completed report is kept until delivered.
pub struct Refreshes {
    polled: Option<Instant>,
    worker: Option<tokio::task::JoinHandle<()>>,
    result: Arc<Mutex<Option<Value>>>,
    work: Option<Value>,
    pub cancelled: Arc<AtomicBool>,
}

impl Default for Refreshes {
    fn default() -> Self {
        Refreshes { polled: None, worker: None, result: Arc::new(Mutex::new(None)), work: None, cancelled: Arc::new(AtomicBool::new(false)) }
    }
}

impl Refreshes {
    /// A read under way or a result not yet delivered: followed on its own timer.
    pub fn busy(&self) -> bool {
        self.result.lock().unwrap().is_some() || self.worker.as_ref().is_some_and(|w| !w.is_finished())
    }

    /// `ask`: whether to ask the server for work; None, every POLL_SECONDS.
    pub async fn poll(&mut self, runner: &Arc<Runner>, ask: Option<bool>) -> Result<(), ApiError> {
        if self.cancelled.load(Ordering::SeqCst) {
            return Ok(());
        }
        let ask = match ask {
            Some(a) => a,
            None if self.polled.is_some_and(|t| t.elapsed().as_secs_f64() < POLL_SECONDS) => return Ok(()),
            None => true,
        };
        self.polled = Some(Instant::now());
        let result = self.result.lock().unwrap().clone();
        if let (Some(result), Some(work)) = (result, self.work.clone()) {
            let mut body = json!({"profile": work["profile"], "runtime": work["runtime"]});
            for (k, v) in result.as_object().cloned().unwrap_or_default() {
                body[k] = v;
            }
            if let Err(e) = runner.client.post(&format!("runner-subscription-refreshes/{}/report", text_of(&work["id"])), &body, None).await {
                if ![404, 409, 422].contains(&e.status) {
                    return Err(e);
                }
            }
            *self.result.lock().unwrap() = None;
            self.work = None;
        }
        if self.worker.as_ref().is_some_and(|w| !w.is_finished()) || !ask {
            return Ok(());
        }
        let wanted = runner.client.get("runner-subscription-refreshes").await?;
        let Some(work) = wanted["refreshes"].as_array().and_then(|a| a.first()).cloned() else { return Ok(()) };
        let config = Config::load(&runner.config.path).unwrap_or_else(|_| runner.config.clone());
        let name = text_of(&work["profile"]);
        // An isolated profile, captured once: never the global or default profile.
        let profile = (!name.is_empty()).then(|| crate::profiles::select(&config.data, None, Some(&name))).flatten();
        let codex = text_of(&work["runtime"]) == "codex";
        self.work = Some(work);
        let (slot, cancelled) = (self.result.clone(), self.cancelled.clone());
        self.worker = Some(tokio::spawn(async move {
            let result = match profile {
                Some(p) if codex => read_codex(&p, &cancelled).await,
                _ => json!({"state": "unavailable"}),
            };
            *slot.lock().unwrap() = Some(result);
        }));
        Ok(())
    }

    pub fn stop(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weekly_window_is_read_and_checked() {
        let now = 1_800_000_000.0;
        let reply = json!({"rateLimits": {"limitId": "codex", "primary": {"windowDurationMins": 300, "usedPercent": 5},
                           "secondary": {"windowDurationMins": 10080, "usedPercent": 100, "resetsAt": now + 3600.0}}});
        let got = weekly(&reply, now);
        assert_eq!(got["state"], "succeeded");
        assert_eq!(got["weekly"]["used_percent"], 100);
        assert_eq!(got["weekly"]["status"], "rejected");
        assert_eq!(got["weekly"]["resets_at"], "2027-01-15T09:00:00+00:00");
        let stale = json!({"rateLimits": {"secondary": {"windowDurationMins": 10080, "usedPercent": 1, "resetsAt": now - 1.0}}});
        assert_eq!(weekly(&stale, now)["state"], "failed");
        assert_eq!(weekly(&json!({"rateLimitsByLimitId": {"other": {}}}), now)["state"], "unavailable");
    }
}
