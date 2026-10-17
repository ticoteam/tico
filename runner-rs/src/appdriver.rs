//! App delivery on a Mac: a bot that lives in another platform's desktop app (a Grok Bot in Grok) gets its
//! messages through `tico-appdriver`, a separate program that drives the app through macOS Accessibility. One
//! driver process per call: one JSON request on stdin, JSON lines on stdout, exit 0 or 1, the last line says which.
//!
//! - send: a turn (crate::hosts::appdriver).
//! - read: the watch loop here, which brings what the person said to the bot in the app itself into Tico.
//! - probe: this computer's readiness `appdriver` field.
//!
//! Only on macOS; elsewhere the feature is absent. The driver runs as the runner's own user, which holds the
//! Accessibility permission, with a home and a PATH only: no turn's credentials ever reach it. One call at a time
//! per app on this computer (`gate`), whoever makes it.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

use crate::client::Client;
use crate::env::Env;
use crate::util::{log, text_of};

pub const EXECUTABLE: &str = "tico-appdriver";
pub const OVERRIDE_ENV: &str = "TICO_APPDRIVER";
/// What a send asks the driver to wait for a reply; the runner allows a minute more before it kills it.
pub const SEND_TIMEOUT_S: u64 = 900;
const PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const READ_LIMIT: u64 = 50;
const PROBE_EVERY: Duration = Duration::from_secs(600);
const WATCH_IDLE: Duration = Duration::from_secs(180);
const WATCH_BUSY: Duration = Duration::from_secs(60);
/// How long after a turn, or a read that found the bot saying something new, the watch stays at WATCH_BUSY.
const WATCH_BUSY_FOR: Duration = Duration::from_secs(30 * 60);
/// The error codes a call reports that the next probe cannot see, or sees no sooner: they stand in readiness
/// until a clean probe or a call that works.
const STICKY: &[&str] = &["no_permission", "not_signed_in", "needs_foreground", "ui_changed"];
/// The contract's error codes: the only ones the server takes in a report.
const CODES: &[&str] = &["no_permission", "not_installed", "not_running", "not_signed_in", "bot_not_found", "busy", "needs_foreground", "ui_changed", "timeout", "needs_human", "send_failed"];

/// Where a bot's app delivery is: the app and the bot's own id and name in it.
#[derive(Clone, Debug, PartialEq)]
pub struct Delivery {
    pub app: String,
    pub bot: Value,
}

/// The one place a bot config (a job's or an assignment's) says its bot is delivered through an app here: a Grok
/// Bot with `app_delivery: {computer, app}` and `app_bot: {id, name}`.
pub fn delivery(config: &Value) -> Option<Delivery> {
    if text_of(&config["harness"]) != "grokbot" || !config["app_delivery"].is_object() {
        return None;
    }
    let app = text_of(&config["app_delivery"]["app"]);
    Some(Delivery { app: if app.is_empty() { "grok".into() } else { app }, bot: config["app_bot"].clone() })
}

/// The app as people know it.
pub fn label(app: &str) -> String {
    match app {
        "grok" | "" => "Grok".into(),
        other => {
            let mut c = other.chars();
            c.next().map(|f| f.to_uppercase().chain(c).collect()).unwrap_or_default()
        }
    }
}

/// The driver: TICO_APPDRIVER, else `tico-appdriver` beside this runner's executable, else on PATH. None on any
/// computer but a Mac.
pub fn driver() -> Option<PathBuf> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    if let Some(p) = std::env::var_os(OVERRIDE_ENV).filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(p)).filter(|p| p.is_file());
    }
    let beside = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join(EXECUTABLE))).filter(|p| p.is_file());
    beside.or_else(|| crate::env::which(EXECUTABLE, &std::env::var("PATH").unwrap_or_default()))
}

/// A home and a PATH, nothing else.
pub fn driver_env() -> Env {
    let mut env = Env::new();
    for key in ["HOME", "PATH"] {
        if let Ok(v) = std::env::var(key) {
            env.insert(key.into(), v);
        }
    }
    env
}

/// The lock every call to `app` on this computer holds: an app has one window to drive.
pub fn gate(app: &str) -> Arc<tokio::sync::Mutex<()>> {
    static GATES: OnceLock<Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>> = OnceLock::new();
    GATES.get_or_init(Default::default).lock().unwrap().entry(app.to_string()).or_default().clone()
}

/// The turn failure for a driver error, in plain words, naming the app.
pub fn failure_line(app: &str, code: &str, detail: &str, bot_name: &str) -> String {
    let app = label(app);
    let base = match code {
        "no_permission" => format!("{app} on this Mac can't be reached: tico-runner needs Accessibility permission (System Settings → Privacy & Security → Accessibility)"),
        "not_installed" => format!("{app} isn't installed on this Mac"),
        "not_running" => format!("{app} isn't running on this Mac and didn't start"),
        "not_signed_in" => format!("{app} on this Mac isn't signed in"),
        "bot_not_found" if !bot_name.is_empty() => format!("{app} on this Mac has no bot named {bot_name}"),
        "bot_not_found" => format!("{app} on this Mac doesn't have this bot"),
        "busy" => format!("{app} on this Mac was still answering something else; nothing was sent"),
        "needs_foreground" => format!("{app} on this Mac answers only while it is in front; bring it forward and send again"),
        "ui_changed" => return format!("{app} changed its layout (ui_changed: {}); nothing was pressed", if detail.is_empty() { "unknown" } else { detail }),
        "timeout" => format!("{app} on this Mac didn't finish its reply in time"),
        "needs_human" => format!("{app} is waiting for a person in the app"),
        "send_failed" => format!("{app} on this Mac didn't take the message"),
        other => format!("{app} on this Mac failed ({})", if other.is_empty() { "no reason given" } else { other }),
    };
    if detail.is_empty() { base } else { format!("{base} ({detail})") }
}

/// One driver call's lines and whether it exited 0. Err is a driver that did not start or ran past `timeout`
/// (it is killed).
pub async fn call(driver: &Path, request: &Value, timeout: Duration) -> Result<(Vec<Value>, bool), String> {
    let mut command = tokio::process::Command::new(driver);
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).env_clear().envs(driver_env()).kill_on_drop(true);
    if let Some(home) = std::env::var_os("HOME") {
        command.current_dir(home);
    }
    let mut child = command.spawn().map_err(|e| format!("could not start {EXECUTABLE}: {e}"))?;
    let body = request.to_string();
    let mut stdin = child.stdin.take();
    let stdout = child.stdout.take();
    let run = async {
        if let Some(stdin) = stdin.as_mut() {
            let _ = stdin.write_all(body.as_bytes()).await;
            let _ = stdin.shutdown().await;
        }
        drop(stdin);
        let mut out = vec![];
        if let Some(stdout) = stdout {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                    out.push(v);
                }
            }
        }
        let ok = child.wait().await.is_ok_and(|s| s.success());
        (out, ok)
    };
    tokio::time::timeout(timeout, run).await.map_err(|_| format!("{EXECUTABLE} did not answer within {}s", timeout.as_secs()))
}

/// The `{code, detail, text}` of a call's error line.
pub fn error_of(line: &Value) -> Value {
    json!({"code": text_of(&line["code"]), "detail": text_of(&line["detail"]), "text": text_of(&line["text"])})
}

// ------------------------------------------------------------------------------------------ what the server hears

#[derive(Default)]
struct Shared {
    /// The last probe: its lines, or why it failed.
    probe: Option<Result<Vec<Value>, String>>,
    /// Per app, the last call's sticky error code (STICKY).
    sticky: HashMap<String, String>,
    /// The last turn's end, or read that found the bot saying something new.
    activity: Option<Instant>,
    /// POSTs to `runners/me/app-delivery/{slug}` the watch loop sends next.
    reports: Vec<(String, Value)>,
}

fn shared() -> std::sync::MutexGuard<'static, Shared> {
    static S: OnceLock<Mutex<Shared>> = OnceLock::new();
    S.get_or_init(Default::default).lock().unwrap_or_else(|p| p.into_inner())
}

fn wake() -> &'static tokio::sync::Notify {
    static W: OnceLock<tokio::sync::Notify> = OnceLock::new();
    W.get_or_init(tokio::sync::Notify::new)
}

/// A call to `app` ended with `code` (None: it worked).
pub fn note_call(app: &str, code: Option<&str>) {
    let mut s = shared();
    match code {
        Some(c) if STICKY.contains(&c) => {
            s.sticky.insert(app.into(), c.into());
        }
        Some(_) => {}
        None => {
            s.sticky.remove(app);
        }
    }
}

/// A turn ended: the watch looks more often for a while.
pub fn touched() {
    shared().activity = Some(Instant::now());
    wake().notify_one();
}

/// Tell the server about a bot's app error (`error` is `{code, detail, text}`): a failed send shows in Health, and
/// `needs_human` gives the bot's person a question.
pub fn report(slug: &str, app: &str, error: Value) {
    if !CODES.contains(&error["code"].as_str().unwrap_or_default()) {
        return;
    }
    shared().reports.push((slug.to_string(), json!({"app": app, "messages": [], "error": error})));
    wake().notify_one();
}

/// The reports waiting to go.
pub fn take_reports() -> Vec<(String, Value)> {
    std::mem::take(&mut shared().reports)
}

/// One bot's waiting reports, leaving the others' (tests run side by side).
#[cfg(test)]
pub fn take_reports_for(slug: &str) -> Vec<Value> {
    let mut s = shared();
    let (mine, rest) = std::mem::take(&mut s.reports).into_iter().partition(|(b, _)| b == slug);
    s.reports = rest;
    mine.into_iter().map(|(_, body)| body).collect()
}

/// The bot configs assigned here that deliver through an app.
fn deliveries(assignments: &[Value]) -> Vec<Delivery> {
    assignments.iter().filter(|e| e["state"] != "archived").filter_map(|e| delivery(&e["config"])).collect()
}

/// This heartbeat's readiness `appdriver` field: only on a Mac where it matters (TICO_APPDRIVER is set or a bot
/// here delivers through an app).
pub fn readiness(assignments: &[Value]) -> Option<Value> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let wanted = deliveries(assignments);
    if wanted.is_empty() && std::env::var_os(OVERRIDE_ENV).is_none_or(|v| v.is_empty()) {
        return None;
    }
    let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "tico-runner".into());
    let s = shared();
    let probe = if driver().is_some() { s.probe.as_ref() } else { None };
    let mut apps: Vec<String> = wanted.into_iter().map(|d| d.app).collect();
    apps.sort();
    apps.dedup();
    Some(document(driver().is_some(), probe, &s.sticky, &apps, &exe))
}

/// The `appdriver` field from the last probe (None: not yet run), the sticky call errors, the apps bots here use
/// and the executable that needs the permission.
pub fn document(driver: bool, probe: Option<&Result<Vec<Value>, String>>, sticky: &HashMap<String, String>, apps: &[String], exe: &str) -> Value {
    if !driver {
        return json!({"trusted": false, "detail": format!("{EXECUTABLE} is not installed beside tico-runner"), "apps": {}});
    }
    let permission = format!("Give {exe} Accessibility permission: System Settings → Privacy & Security → Accessibility");
    let lines = match probe {
        None => return json!({"trusted": false, "detail": "Checking the apps on this Mac", "apps": {}}),
        Some(Err(why)) => {
            let mut out = Map::new();
            for app in apps.iter().map(String::as_str).chain(apps.is_empty().then_some("grok")) {
                out.insert(app.into(), app_row(&Value::Null, "error", &format!("{EXECUTABLE} could not check {}: {why}", label(app))));
            }
            return json!({"trusted": false, "detail": format!("{EXECUTABLE} could not check this Mac: {why}"), "apps": out});
        }
        Some(Ok(lines)) => lines,
    };
    let summary = lines.iter().rev().find(|l| l["type"] == "probe").cloned().unwrap_or(Value::Null);
    let trusted = summary["trusted"] == true;
    let mut out = Map::new();
    for line in lines.iter().filter(|l| l["type"] == "app") {
        let app = text_of(&line["app"]);
        if app.is_empty() {
            continue;
        }
        let name = label(&app);
        let (state, detail) = if !trusted {
            ("no_permission".to_string(), permission.clone())
        } else if line["installed"] != true {
            ("not_installed".into(), format!("{name} is not installed"))
        } else if line["signed_in"] != true {
            ("not_signed_in".into(), format!("Sign in to {name}"))
        } else if line["background_ok"] != true {
            ("needs_foreground".into(), format!("{name} answers only while it is in front on this Mac"))
        } else {
            ("ready".into(), "Ready".into())
        };
        let (state, detail) = match sticky.get(&app) {
            Some(code) if code == "no_permission" => (code.clone(), permission.clone()),
            Some(code) => (code.clone(), failure_line(&app, code, "", "")),
            None => (state, detail),
        };
        out.insert(app, app_row(line, &state, &detail));
    }
    let mut doc = json!({"trusted": trusted, "macos": text_of(&summary["macos"]), "detail": if trusted { String::new() } else { permission }, "apps": out});
    if summary.is_null() {
        doc["detail"] = json!(format!("{EXECUTABLE} gave no probe result"));
    }
    doc
}

fn app_row(line: &Value, state: &str, detail: &str) -> Value {
    let flag = |k: &str| line[k] == true;
    json!({
        "installed": flag("installed"),
        "version": text_of(&line["version"]),
        "running": flag("running"),
        "signed_in": flag("signed_in"),
        "background_ok": flag("background_ok"),
        "ready": state == "ready",
        "state": state,
        "detail": detail,
    })
}

// ------------------------------------------------------------------------------------------ the watch

/// How long the watch waits before its next read: a minute for half an hour after a turn or a new bot message,
/// else three.
pub fn interval(since_activity: Option<Duration>) -> Duration {
    match since_activity {
        Some(d) if d < WATCH_BUSY_FOR => WATCH_BUSY,
        _ => WATCH_IDLE,
    }
}

/// What one watch pass found.
#[derive(Debug, PartialEq)]
pub enum Pass {
    /// The server has no app-delivery endpoint (an older one): the watch stops until the runner restarts.
    Unsupported,
    /// Whether a read found the bot saying something new.
    Done(bool),
}

/// Read each app-delivery bot's new messages and post them, with the error the read hit. A read whose app has a
/// turn running waits for the next pass. Message text is never logged.
pub async fn watch_pass(client: &Client, driver: &Path) -> Pass {
    let list = match client.get("runners/me/app-delivery").await {
        Ok(v) => v,
        Err(e) if e.status == 404 => return Pass::Unsupported,
        Err(e) => {
            crate::util::diagnostic(&format!("Tico runner: app delivery list failed ({})", crate::runner::describe(&e)));
            return Pass::Done(false);
        }
    };
    let mut fresh = false;
    for row in list["bots"].as_array().into_iter().flatten() {
        let (slug, app) = (text_of(&row["slug"]), text_of(&row["app"]));
        if slug.is_empty() || app.is_empty() {
            continue;
        }
        let read = {
            let Ok(_held) = gate(&app).try_lock_owned() else { continue };
            let request = json!({"op": "read", "app": app, "bot": row["bot"], "after": row["after"], "limit": READ_LIMIT});
            call(driver, &request, READ_TIMEOUT).await
        };
        let (messages, error) = read_result(read);
        note_call(&app, error.as_ref().map(|e| e["code"].as_str().unwrap_or_default()));
        if messages.is_empty() && error.is_none() {
            continue;
        }
        let bot_spoke = messages.iter().any(|m| m["role"] == "bot");
        let body = json!({"app": app, "messages": messages, "error": error});
        match client.post(&format!("runners/me/app-delivery/{slug}"), &body, None).await {
            Ok(_) => fresh |= bot_spoke,
            Err(e) => crate::util::diagnostic(&format!("Tico runner: {slug}: app delivery post failed ({})", crate::runner::describe(&e))),
        }
    }
    Pass::Done(fresh)
}

/// A read's messages, as the server takes them, and its error.
fn read_result(read: Result<(Vec<Value>, bool), String>) -> (Vec<Value>, Option<Value>) {
    match read {
        Err(why) => (vec![], Some(json!({"code": "timeout", "detail": why, "text": ""}))),
        Ok((lines, ok)) => {
            // Only what the server takes: a message without its key, role or text would refuse the whole report.
            let messages = lines
                .iter()
                .filter(|l| l["type"] == "msg" && (l["role"] == "bot" || l["role"] == "user") && !text_of(&l["key"]).is_empty() && !text_of(&l["text"]).is_empty())
                .map(|m| json!({"key": text_of(&m["key"]), "role": text_of(&m["role"]), "text": text_of(&m["text"]), "at": m["at"], "needs_human": false}))
                .collect();
            let error = lines.iter().rev().find(|l| l["type"] == "error").map(error_of).filter(|e| CODES.contains(&e["code"].as_str().unwrap_or_default()));
            if !ok && error.is_none() {
                crate::util::diagnostic(&format!("Tico runner: {EXECUTABLE} read failed without a known error code"));
            }
            (messages, error)
        }
    }
}

async fn probe(driver: &Path) {
    let result = {
        let _held = gate("grok").lock_owned().await;
        call(driver, &json!({"op": "probe"}), PROBE_TIMEOUT).await
    };
    let result = match result {
        Ok((lines, true)) => Ok(lines),
        Ok((lines, false)) => Err(lines.iter().rev().find(|l| l["type"] == "error").map(|l| text_of(&l["detail"])).filter(|d| !d.is_empty()).unwrap_or_else(|| "the probe failed".into())),
        Err(why) => Err(why),
    };
    let mut s = shared();
    if result.is_ok() {
        s.sticky.clear();
    }
    s.probe = Some(result);
}

async fn send_reports(client: &Client) {
    for (slug, body) in take_reports() {
        if let Err(e) = client.post(&format!("runners/me/app-delivery/{slug}"), &body, None).await {
            if e.status != 404 {
                crate::util::diagnostic(&format!("Tico runner: {slug}: app delivery report failed ({})", crate::runner::describe(&e)));
            }
        }
    }
}

/// The watch and the probe, for as long as the runner runs. Nothing at all on a computer without the driver.
pub async fn run_loop(runner: Arc<crate::runner::Runner>) {
    if !cfg!(target_os = "macos") {
        return;
    }
    let (mut next_read, mut next_probe) = (Instant::now(), Instant::now());
    let mut watching = true;
    while !runner.stopping() {
        send_reports(&runner.client).await;
        let assignments = runner.assignments_seen();
        let wanted = !deliveries(&assignments).is_empty() || std::env::var_os(OVERRIDE_ENV).is_some_and(|v| !v.is_empty());
        if let Some(driver) = driver().filter(|_| wanted) {
            if Instant::now() >= next_probe {
                probe(&driver).await;
                next_probe = Instant::now() + PROBE_EVERY;
            }
            if watching && Instant::now() >= next_read {
                match watch_pass(&runner.client, &driver).await {
                    Pass::Unsupported => {
                        log("Tico runner: this server does not take app delivery reads; the watch is off until the runner restarts");
                        watching = false;
                    }
                    Pass::Done(true) => touched(),
                    Pass::Done(false) => {}
                }
                let since = shared().activity.map(|a| a.elapsed());
                next_read = Instant::now() + interval(since);
            }
        }
        let since = shared().activity.map(|a| a.elapsed());
        // A turn's end brings the next read forward to the busy interval.
        next_read = next_read.min(Instant::now() + interval(since));
        let until = next_read.min(next_probe).saturating_duration_since(Instant::now()).max(Duration::from_secs(1));
        let _ = tokio::time::timeout(until.min(Duration::from_secs(30)), wake().notified()).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_watch_reads_every_minute_for_half_an_hour_after_activity_then_every_three() {
        assert_eq!(interval(None), Duration::from_secs(180));
        assert_eq!(interval(Some(Duration::from_secs(5))), Duration::from_secs(60));
        assert_eq!(interval(Some(Duration::from_secs(29 * 60))), Duration::from_secs(60));
        assert_eq!(interval(Some(Duration::from_secs(30 * 60))), Duration::from_secs(180));
    }

    #[test]
    fn the_probe_becomes_readiness_and_a_sticky_call_error_overrides_it() {
        let exe = "/Applications/Tico.app/Contents/MacOS/tico-runner";
        let app = |signed_in: bool, background_ok: bool| {
            json!({"type": "app", "app": "grok", "installed": true, "version": "1.4", "running": true, "signed_in": signed_in, "background_ok": background_ok})
        };
        let probe = |trusted: bool, line: Value| Ok(vec![line, json!({"type": "probe", "trusted": trusted, "macos": "15.1"})]);
        let none = HashMap::new();
        let grok = vec!["grok".to_string()];
        let ready = document(true, Some(&probe(true, app(true, true))), &none, &grok, exe);
        assert_eq!(
            ready,
            json!({"trusted": true, "macos": "15.1", "detail": "", "apps": {"grok": {"installed": true, "version": "1.4", "running": true, "signed_in": true,
                   "background_ok": true, "ready": true, "state": "ready", "detail": "Ready"}}})
        );
        let untrusted = document(true, Some(&probe(false, app(true, true))), &none, &grok, exe);
        assert_eq!(untrusted["apps"]["grok"]["state"], "no_permission");
        assert!(text_of(&untrusted["detail"]).starts_with(&format!("Give {exe} Accessibility permission")));
        let signed_out = document(true, Some(&probe(true, app(false, true))), &none, &grok, exe);
        assert_eq!((signed_out["apps"]["grok"]["state"].clone(), signed_out["apps"]["grok"]["detail"].clone()), (json!("not_signed_in"), json!("Sign in to Grok")));
        assert_eq!(document(true, Some(&probe(true, app(true, false))), &none, &grok, exe)["apps"]["grok"]["state"], "needs_foreground");
        let sticky = HashMap::from([("grok".to_string(), "ui_changed".to_string())]);
        let changed = document(true, Some(&probe(true, app(true, true))), &sticky, &grok, exe);
        assert_eq!((changed["apps"]["grok"]["state"].clone(), changed["apps"]["grok"]["ready"].clone()), (json!("ui_changed"), json!(false)));
        assert_eq!(document(true, Some(&Err("it crashed".into())), &none, &grok, exe)["apps"]["grok"]["state"], "error");
        assert_eq!(document(false, None, &none, &grok, exe), json!({"trusted": false, "detail": "tico-appdriver is not installed beside tico-runner", "apps": {}}));
    }

    #[test]
    fn only_a_grok_bot_with_app_delivery_is_delivered_through_the_app() {
        let bot = json!({"id": "b-1", "name": "Acme Helper"});
        let config = json!({"harness": "grokbot", "runtime": "grokbot", "app_delivery": {"computer": "c-1", "app": "grok"}, "app_bot": bot});
        assert_eq!(delivery(&config), Some(Delivery { app: "grok".into(), bot }));
        assert_eq!(delivery(&json!({"harness": "grokbot", "runtime": "grokbot"})), None);
        assert_eq!(delivery(&json!({"harness": "claude-code", "app_delivery": {"app": "grok"}})), None);
    }

    /// A hub answering the list, then taking one post, recording both requests.
    async fn hub(list: Value) -> (String, Arc<Mutex<Vec<String>>>) {
        use tokio::io::AsyncReadExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let log_ = seen.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else { return };
                let (mut got, mut buf) = (vec![], vec![0u8; 65536]);
                loop {
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    if n == 0 {
                        break;
                    }
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    if let Some(end) = text.find("\r\n\r\n") {
                        let length = text[..end].lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                        if got.len() >= end + 4 + length {
                            break;
                        }
                    }
                }
                let text = String::from_utf8_lossy(&got).to_string();
                let body = if text.starts_with("GET ") { list.to_string() } else { json!({"after": "k2", "posted": 2}).to_string() };
                log_.lock().unwrap().push(text);
                let reply = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });
        (url, seen)
    }

    #[tokio::test]
    async fn a_read_from_the_servers_cursor_is_posted_to_the_bot() {
        let dir = std::env::temp_dir().join(format!("fake-appdriver-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let driver = dir.join("tico-appdriver");
        let lines = [
            json!({"type": "msg", "key": "k1", "role": "user", "text": "When is the launch?", "at": "2026-10-01T10:00:00Z"}),
            json!({"type": "msg", "key": "k2", "role": "bot", "text": "Thursday.", "at": "2026-10-01T10:00:05Z"}),
        ];
        let body: String = lines.iter().map(|l| format!("echo '{l}'\n")).collect();
        crate::util::write_script(&driver, &format!("#!/bin/sh\ncat > '{}'\n{body}exit 0\n", dir.join("request.json").display()));
        let bot = json!({"id": "b-1", "name": "Acme Helper"});
        // An app of its own: the host tests beside it hold Grok's gate, and a held gate skips the read.
        let (url, seen) = hub(json!({"bots": [{"slug": "helper", "app": "acme-chat", "bot": bot, "after": "k0"}]})).await;
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        assert_eq!(watch_pass(&client, &driver).await, Pass::Done(true));
        let request: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("request.json")).unwrap()).unwrap();
        assert_eq!(request, json!({"op": "read", "app": "acme-chat", "bot": bot, "after": "k0", "limit": 50}));
        let seen = seen.lock().unwrap();
        assert!(seen[0].starts_with("GET /api/v2/runners/me/app-delivery "));
        assert!(seen[1].starts_with("POST /api/v2/runners/me/app-delivery/helper "));
        let posted: Value = serde_json::from_str(&seen[1][seen[1].find("\r\n\r\n").unwrap() + 4..]).unwrap();
        assert_eq!(posted["error"], Value::Null);
        assert_eq!(posted["messages"][1], json!({"key": "k2", "role": "bot", "text": "Thursday.", "at": "2026-10-01T10:00:05Z", "needs_human": false}));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
