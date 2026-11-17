//! Read a subscription's weekly allowance through the signed-in CLI, without a thread, turn or paid request
//! (runner/subscription_refresh.py): `codex app-server`'s `account/rateLimits/read`, relayed for the server's
//! runner-subscription-refreshes. Only fixed states travel back, never a provider's error text.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::client::ApiError;
use crate::runner::Runner;
use crate::util::{iso, parse_iso, text_of};

pub const POLL_SECONDS: f64 = 15.0;
const TIMEOUT: Duration = Duration::from_secs(15);

fn now_iso_micros() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let base = text_of(&iso(&json!(now.as_secs())));
    // Python's datetime.now(timezone.utc).isoformat(): microseconds before the offset.
    base.replace("+00:00", &format!(".{:06}+00:00", now.subsec_micros()))
}

pub(crate) async fn rpc(
    stdin: &mut tokio::process::ChildStdin,
    lines: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    id: u64,
    method: &str,
    params: Value,
) -> Result<Value, ()> {
    let msg =
        json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string() + "\n";
    stdin.write_all(msg.as_bytes()).await.map_err(|_| ())?;
    let read = async {
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(v) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            if v.get("method").is_none() && v["id"] == json!(id) {
                return if v.get("error").is_some() {
                    Err(())
                } else {
                    Ok(v["result"].clone())
                };
            }
        }
        Err(())
    };
    tokio::time::timeout(TIMEOUT, read).await.map_err(|_| ())?
}

/// The weekly window from a `account/rateLimits/read` reply, as the report's fields.
pub fn weekly(reply: &Value, now: f64) -> Value {
    let buckets = &reply["rateLimitsByLimitId"];
    let bucket = if buckets.is_object() {
        &buckets["codex"]
    } else {
        &reply["rateLimits"]
    };
    if !bucket.is_object() || !(bucket["limitId"].is_null() || bucket["limitId"] == "codex") {
        return json!({"state": "unavailable"});
    }
    let windows: Vec<&Value> = [&bucket["primary"], &bucket["secondary"]]
        .into_iter()
        .filter(|w| w.is_object() && w["windowDurationMins"] == json!(10080))
        .collect();
    if windows.len() != 1 {
        return json!({"state": "unavailable"});
    }
    let window = windows[0];
    let percent = &window["usedPercent"];
    let reset = iso(&window["resetsAt"]);
    let p = percent
        .as_f64()
        .filter(|p| percent.is_number() && p.is_finite() && (0.0..=100.0).contains(p));
    let Some(p) = p else {
        return json!({"state": "failed"});
    };
    let left = if reset.is_null() {
        -1.0
    } else {
        parse_iso(&text_of(&reset)) - now
    };
    if !(left > 0.0 && left <= 8.0 * 86400.0) {
        return json!({"state": "failed"});
    }
    json!({"state": "succeeded", "weekly": {"used_percent": percent, "resets_at": reset, "reported_at": now_iso_micros(),
           "status": if p >= 100.0 { json!("rejected") } else { Value::Null }}})
}

/// Every window of a `account/rateLimits/read` reply, kept as a turn's would be (the 5-hour one included), with
/// its plan.
pub(crate) fn remember_windows(profile: &str, reply: &Value) {
    let buckets = &reply["rateLimitsByLimitId"];
    let bucket = if buckets.is_object() {
        &buckets["codex"]
    } else {
        &reply["rateLimits"]
    };
    if !bucket.is_object() || !(bucket["limitId"].is_null() || bucket["limitId"] == "codex") {
        return;
    }
    for w in [&bucket["primary"], &bucket["secondary"]]
        .into_iter()
        .filter(|w| w.is_object())
    {
        // The computer's own login's read, never a turn's: it counts for a plan only where the plan's account is known.
        let event = json!({"used_percent": w.get("usedPercent").cloned().unwrap_or(Value::Null),
                           "window_minutes": w.get("windowDurationMins").cloned().unwrap_or(Value::Null),
                           "resets_at": iso(&w["resetsAt"]), "plan_type": bucket.get("planType").cloned().unwrap_or(Value::Null),
                           "origin": "login"});
        crate::profiles::remember_weekly(profile, "codex", &event);
    }
}

/// One read, with the profile's own Codex home and no API keys.
pub async fn read_codex(profile: &crate::profiles::Profile, canceled: &AtomicBool) -> Value {
    let Some(exe) = crate::env::which("codex", &std::env::var("PATH").unwrap_or_default()) else {
        return json!({"state": "unavailable"});
    };
    let process: crate::env::Env = std::env::vars().collect();
    let mut env = profile.environment("codex", &process);
    for name in [
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ] {
        env.remove(name);
    }
    if canceled.load(Ordering::SeqCst) {
        return json!({"state": "failed"});
    }
    // A sign-out or sign-in after this point (accounts::forget) makes this read's answers stale: they are not kept.
    let since = crate::accounts::read_started(&profile.name, "codex");
    // The bot user's, like the profile's sign-in and turns: app-server writes in CODEX_HOME.
    let mut cmd = crate::isolation::command(exe);
    cmd.arg("app-server")
        .env_clear()
        .envs(&env)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    let Ok(mut child) = cmd.spawn() else {
        return json!({"state": "failed"});
    };
    let (Some(mut stdin), Some(stdout)) = (child.stdin.take(), child.stdout.take()) else {
        return json!({"state": "failed"});
    };
    let mut lines = BufReader::new(stdout).lines();
    let mut account = None;
    let result = async {
        rpc(&mut stdin, &mut lines, 1, "initialize", json!({"clientInfo": {"name": "tico-keeper", "title": "Tico keeper", "version": "0.1"}})).await?;
        let note = json!({"jsonrpc": "2.0", "method": "initialized", "params": {}}).to_string() + "\n";
        stdin.write_all(note.as_bytes()).await.map_err(|_| ())?;
        if canceled.load(Ordering::SeqCst) {
            return Err(());
        }
        let limits = rpc(&mut stdin, &mut lines, 2, "account/rateLimits/read", json!({})).await?;
        // The same session names the account and plan, read without a token refresh (crate::accounts).
        if let Ok(reply) = rpc(&mut stdin, &mut lines, 3, "account/read", json!({"refreshToken": false})).await {
            account = crate::accounts::from_app_server(&reply);
        }
        Ok(limits)
    }
    .await;
    // A read begun before a sign-in or sign-out is someone else's usage: not kept here, and not reported either.
    let current = crate::accounts::keep_if_current(&profile.name, "codex", since, || {
        if let Some(account) = account {
            crate::accounts::remember(&profile.name, "codex", account);
        }
        if let Ok(reply) = &result {
            remember_windows(&profile.name, reply);
        }
    });
    drop(stdin);
    if tokio::time::timeout(Duration::from_secs(5), child.wait())
        .await
        .is_err()
    {
        let _ = child.kill().await;
    }
    // Again after the child's shutdown wait: a sign-in during it makes this read someone else's as well.
    let current = current && crate::accounts::read_started(&profile.name, "codex") == since;
    match result {
        Ok(reply) if current => weekly(&reply, crate::util::epoch_s()),
        _ => json!({"state": "failed"}),
    }
}

/// One read of a Claude plan's week: a one-line prompt on Haiku with its setup-token, in an empty home of the read's
/// own, so no login, key or setting of the computer's is read or written (the home is removed after). A setup-token may
/// only run prompts, so `/usage` refuses it; the usage windows come instead from the `rate_limit_event` Claude Code
/// prints for any turn (`hosts::claude::rate_limit_windows`, as a turn's reading). It spends a few tokens of the plan.
/// Any failure is a fixed state, never the CLI's own text.
pub async fn read_claude_plan(token: &str, canceled: &AtomicBool) -> Value {
    use std::os::unix::fs::PermissionsExt;

    let Some(exe) = crate::env::which("claude", &std::env::var("PATH").unwrap_or_default()) else {
        return json!({"state": "unavailable"});
    };
    if canceled.load(Ordering::SeqCst) {
        return json!({"state": "failed"});
    }
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    let home = std::env::temp_dir().join(format!("tico-plan-usage-{}-{nanos}", std::process::id()));
    if std::fs::create_dir_all(&home).is_err() {
        return json!({"state": "failed"});
    }
    let _ = std::fs::set_permissions(&home, std::fs::Permissions::from_mode(0o700));
    let mut env: crate::env::Env = std::env::vars().collect();
    for name in [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ] {
        env.remove(name);
    }
    let dir = home.display().to_string();
    env.insert("CLAUDE_CODE_OAUTH_TOKEN".into(), token.to_string());
    env.insert("CLAUDE_CONFIG_DIR".into(), dir.clone());
    env.insert("HOME".into(), dir);
    let mut cmd = crate::isolation::command(exe);
    cmd.args([
        "-p",
        "Reply with OK.",
        "--model",
        "haiku",
        "--max-turns",
        "1",
        "--output-format",
        "stream-json",
        "--verbose",
    ])
    .env_clear()
    .envs(&env)
    .current_dir(&home)
    .stdin(std::process::Stdio::null())
    .stdout(std::process::Stdio::piped())
    .stderr(std::process::Stdio::null())
    .kill_on_drop(true);
    let reply = tokio::time::timeout(Duration::from_secs(90), cmd.output()).await;
    let _ = std::fs::remove_dir_all(&home);
    match reply {
        Ok(Ok(out)) if !canceled.load(Ordering::SeqCst) => plan_week(&out.stdout),
        _ => json!({"state": "failed"}),
    }
}

/// The plan's week from a turn's stream-json lines: the last `rate_limit_event`'s seven-day window with both its used
/// percent and its reset. Anything else is failed.
pub(crate) fn plan_week(stdout: &[u8]) -> Value {
    let week = String::from_utf8_lossy(stdout)
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|msg| msg["type"] == "rate_limit_event")
        .flat_map(|msg| crate::hosts::claude::rate_limit_windows(&msg["rate_limit_info"]))
        .rfind(|w| {
            w["window_minutes"] == 7 * 24 * 60
                && w["used_percent"].is_number()
                && w["resets_at"].is_string()
        });
    match week {
        Some(w) => {
            json!({"state": "succeeded", "plan": {"used_percent": w["used_percent"], "resets_at": w["resets_at"]}})
        }
        None => json!({"state": "failed"}),
    }
}

/// One bounded worker per computer; a completed report is kept until delivered.
pub struct Refreshes {
    polled: Option<Instant>,
    worker: Option<tokio::task::JoinHandle<()>>,
    result: Arc<Mutex<Option<Value>>>,
    work: Option<Value>,
    pub canceled: Arc<AtomicBool>,
}

impl Default for Refreshes {
    fn default() -> Self {
        Refreshes {
            polled: None,
            worker: None,
            result: Arc::new(Mutex::new(None)),
            work: None,
            canceled: Arc::new(AtomicBool::new(false)),
        }
    }
}

impl Refreshes {
    /// A read under way or a result not yet delivered: followed on its own timer.
    pub fn busy(&self) -> bool {
        self.result.lock().unwrap().is_some()
            || self.worker.as_ref().is_some_and(|w| !w.is_finished())
    }

    /// `ask`: whether to ask the server for work; None, every POLL_SECONDS.
    pub async fn poll(&mut self, runner: &Arc<Runner>, ask: Option<bool>) -> Result<(), ApiError> {
        if self.canceled.load(Ordering::SeqCst) {
            return Ok(());
        }
        let ask = match ask {
            Some(a) => a,
            None if self
                .polled
                .is_some_and(|t| t.elapsed().as_secs_f64() < POLL_SECONDS) =>
            {
                return Ok(())
            }
            None => true,
        };
        self.polled = Some(Instant::now());
        let result = self.result.lock().unwrap().clone();
        if let (Some(result), Some(work)) = (result, self.work.clone()) {
            let mut body = json!({"profile": work["profile"], "runtime": work["runtime"]});
            if let Some(sid) = work["subscription"].as_str().filter(|s| !s.is_empty()) {
                body["subscription"] = json!(sid);
            }
            for (k, v) in result.as_object().cloned().unwrap_or_default() {
                body[k] = v;
            }
            if let Err(e) = runner
                .client
                .post(
                    &format!(
                        "runner-subscription-refreshes/{}/report",
                        text_of(&work["id"])
                    ),
                    &body,
                    None,
                )
                .await
            {
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
        let Some(work) = wanted["refreshes"]
            .as_array()
            .and_then(|a| a.first())
            .cloned()
        else {
            return Ok(());
        };
        let config = runner.config_now();
        let name = text_of(&work["profile"]);
        // An isolated profile, captured once: never the global or default profile.
        let profile = (!name.is_empty())
            .then(|| crate::profiles::select(&config.data, Some(&name)))
            .flatten();
        // Only a runtime whose weekly window a refresh can read (`weekly_usage = "refresh"`).
        let refreshable =
            crate::harness_tools::caps(&text_of(&work["runtime"]), "").weekly_usage == "refresh";
        // A plan's read (backend/subscription_refresh.py `request_plan_read`): its setup-token travels in the work, for this read only.
        let plan = work["subscription"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(|_| text_of(&work["env"]["CLAUDE_CODE_OAUTH_TOKEN"]));
        self.work = Some(work);
        let (slot, canceled) = (self.result.clone(), self.canceled.clone());
        self.worker = Some(tokio::spawn(async move {
            let result = match (plan, profile) {
                (Some(token), _) if !token.is_empty() => read_claude_plan(&token, &canceled).await,
                (Some(_), _) => json!({"state": "unavailable"}),
                (None, Some(p)) if refreshable => read_codex(&p, &canceled).await,
                _ => json!({"state": "unavailable"}),
            };
            *slot.lock().unwrap() = Some(result);
        }));
        Ok(())
    }

    pub fn stop(&self) {
        self.canceled.store(true, Ordering::SeqCst);
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
        assert_eq!(
            weekly(&json!({"rateLimitsByLimitId": {"other": {}}}), now)["state"],
            "unavailable"
        );
    }

    #[test]
    fn plan_week_reads_the_seven_day_window_of_a_turn() {
        let lines = concat!(
            r#"{"type":"system","subtype":"init"}"#,
            "\n",
            r#"{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"five_hour","resetsAt":1795000000,"unifiedWindows":{"five_hour":{"utilization":0.03,"resetsAt":1795000000},"seven_day":{"utilization":0.42,"resetsAt":1795400000}}}}"#,
            "\n",
            r#"{"type":"result","result":"OK"}"#,
            "\n",
        );
        let got = plan_week(lines.as_bytes());
        assert_eq!(got["state"], "succeeded");
        assert_eq!(got["plan"]["used_percent"], 42.0);
        assert!(got["plan"]["resets_at"]
            .as_str()
            .is_some_and(|s| s.starts_with("2026-")));
        assert_eq!(plan_week(b"not json")["state"], "failed");
        let no_week = r#"{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"five_hour","resetsAt":1795000000}}"#;
        assert_eq!(plan_week(no_week.as_bytes())["state"], "failed");
    }
}
