//! The app-delivery host: one `tico-appdriver` send per turn (crate::appdriver), which gives the person's message
//! to the bot in its app on this Mac and reads the reply back:
//!
//!     {"op":"send","app":"grok","bot":{"id","name"},"text":"…","timeout_s":900}
//!
//! goes in on stdin; `delta` lines stream the reply so far, `message` is the reply, `error` ends the turn with a
//! plain line naming the app. `needs_human` is the app's own reply asking for a person: it is the bot's reply, and
//! the server is told so the bot's person gets the question. The app holds the conversation, so there is no thread
//! to keep, no steering, and an interrupt kills the driver and leaves the app as it is.

use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};

use super::oneshot::{OneShot, Thread, Turn, Wire};
use super::Settings;
use crate::appdriver::{self, Delivery};
use crate::env::Env;
use crate::util::text_of;

pub struct AppDriver;

/// The command a host holds: the driver, the app, and the bot in it as JSON.
pub fn command(driver: &std::path::Path, delivery: &Delivery) -> Vec<String> {
    vec![driver.display().to_string(), delivery.app.clone(), delivery.bot.to_string()]
}

fn app(cmd: &[String]) -> &str {
    cmd.get(1).map_or("", String::as_str)
}

fn bot(cmd: &[String]) -> Value {
    cmd.get(2).and_then(|b| serde_json::from_str(b).ok()).unwrap_or(Value::Null)
}

impl Wire for AppDriver {
    const NAME: &'static str = "appdriver";
    const PROGRAM: &'static str = appdriver::EXECUTABLE;
    /// The runner's own user holds the Accessibility permission, and the driver runs no bot code.
    const ISOLATED: bool = false;

    fn argv(cmd: &[String], _thread_id: &str, _t: &Thread, _effort: Option<&str>) -> Vec<String> {
        cmd[..1].to_vec()
    }

    fn env(_settings: &Settings) -> Env {
        appdriver::driver_env()
    }

    fn stdin(cmd: &[String], text: &str) -> String {
        json!({"op": "send", "app": app(cmd), "bot": bot(cmd), "text": text, "timeout_s": appdriver::SEND_TIMEOUT_S}).to_string()
    }

    fn gate(cmd: &[String]) -> Option<Arc<tokio::sync::Mutex<()>>> {
        Some(appdriver::gate(app(cmd)))
    }

    fn deadline(_cmd: &[String]) -> Option<Duration> {
        #[cfg(test)]
        if let Some(short) = tests::DEADLINE.with(|d| d.get()) {
            return Some(short);
        }
        Some(Duration::from_secs(appdriver::SEND_TIMEOUT_S + 60))
    }

    fn timed_out(turn: &mut Turn) {
        Self::on_event(&json!({"type": "error", "code": "timeout", "detail": ""}), turn);
    }

    fn on_event(msg: &Value, turn: &mut Turn) {
        match msg["type"].as_str().unwrap_or_default() {
            "delta" => {
                let text = text_of(&msg["text"]);
                if !text.is_empty() {
                    turn.reply = text.clone();
                    turn.emit("message", json!({"text": text, "final": false}));
                }
            }
            "message" => {
                turn.ended = true;
                turn.reply = text_of(&msg["text"]);
            }
            "error" => {
                turn.ended = true;
                let code = text_of(&msg["code"]);
                turn.extra = appdriver::error_of(msg);
                if code == "needs_human" {
                    turn.reply = text_of(&msg["text"]);
                } else {
                    let name = text_of(&bot(&turn.cmd)["name"]);
                    turn.error = appdriver::failure_line(app(&turn.cmd), &code, &text_of(&msg["detail"]), &name);
                }
            }
            _ => {}
        }
    }

    fn succeeded(_rc: i32, turn: &Turn) -> bool {
        turn.ended && turn.error.is_empty()
    }

    fn finished(rc: i32, turn: &Turn, interrupted: bool) {
        appdriver::touched();
        if interrupted {
            return;
        }
        let app = app(&turn.cmd);
        let code = text_of(&turn.extra["code"]);
        if code.is_empty() {
            if Self::succeeded(rc, turn) {
                appdriver::note_call(app, None);
            }
            return;
        }
        appdriver::note_call(app, Some(&code));
        appdriver::report(&turn.bot, app, turn.extra.clone());
    }
}

pub type AppDriverHost = OneShot<AppDriver>;

impl AppDriverHost {
    /// The bot in its app: `appdriver:<app>:<bot id>`. The app keeps the conversation itself.
    fn thread_id(&self) -> String {
        let cmd = self.cmd();
        format!("appdriver:{}:{}", app(cmd), text_of(&bot(cmd)["id"]))
    }

    pub fn start_thread(&self, settings: &Settings) -> String {
        self.record(&self.thread_id(), settings, true)
    }

    pub fn resume_thread(&self, _thread_id: &str, settings: &Settings) -> String {
        self.start_thread(settings)
    }
}

#[cfg(test)]
mod tests {
    use super::super::oneshot::testing::turn_events;
    use super::*;
    use tokio::sync::mpsc;

    thread_local! {
        /// A shorter send deadline for this test's thread (its current-thread runtime runs the host's reader).
        pub static DEADLINE: std::cell::Cell<Option<Duration>> = const { std::cell::Cell::new(None) };
    }

    /// A driver that saves its request, prints `lines` and exits `rc` (or, with `hang`, waits to be killed).
    fn driver(dir: &std::path::Path, name: &str, lines: &[Value], rc: i32, hang: bool) -> std::path::PathBuf {
        let path = dir.join(name);
        let out: String = lines.iter().map(|l| format!("echo '{l}'\n")).collect();
        let tail = if hang { format!("echo $$ > '{}'\nexec sleep 30\n", dir.join(format!("{name}.pid")).display()) } else { format!("exit {rc}\n") };
        crate::util::write_script(&path, &format!("#!/bin/sh\ncat > '{}'\n{out}{tail}", dir.join(format!("{name}.request")).display()));
        path
    }

    fn host(slug: &str, driver: &std::path::Path, tx: super::super::Events) -> AppDriverHost {
        let delivery = Delivery { app: "grok".into(), bot: json!({"id": "b-1", "name": "Acme Helper"}) };
        let h = AppDriverHost::new(slug, command(driver, &delivery), tx);
        h.start();
        h
    }

    fn settings(dir: &std::path::Path) -> Settings {
        Settings { cwd: dir.display().to_string(), ..Default::default() }
    }

    fn strip(events: Vec<Value>) -> Vec<Value> {
        events.into_iter().filter(|e| e["kind"] != "status").collect()
    }

    #[tokio::test]
    async fn a_send_streams_its_reply_and_every_error_fails_the_turn_in_plain_words() {
        let dir = std::env::temp_dir().join(format!("fake-appdriver-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let good = [
            json!({"type": "status", "state": "sending"}),
            json!({"type": "delta", "text": "Thurs"}),
            json!({"type": "delta", "text": "Thursday at ten."}),
            json!({"type": "message", "text": "Thursday at ten.", "final": true}),
        ];
        let (tx, mut rx) = mpsc::unbounded_channel();
        let h = host("helper", &driver(&dir, "good", &good, 0, false), tx);
        let thread = h.start_thread(&settings(&dir));
        assert_eq!(thread, "appdriver:grok:b-1");
        h.start_turn(&thread, "When is the launch?", None).unwrap();
        assert_eq!(
            strip(turn_events(&mut rx).await),
            [
                json!({"kind": "message", "text": "Thurs", "final": false}),
                json!({"kind": "message", "text": "Thursday at ten.", "final": false}),
                json!({"kind": "message", "text": "Thursday at ten.", "final": true}),
                json!({"kind": "turn_completed", "status": "completed"}),
            ]
        );
        let request: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("good.request")).unwrap()).unwrap();
        assert_eq!(request, json!({"op": "send", "app": "grok", "bot": {"id": "b-1", "name": "Acme Helper"}, "text": "When is the launch?", "timeout_s": 900}));
        h.stop().await;

        let cases = [
            ("no_permission", "", "Grok on this Mac can't be reached: tico-runner needs Accessibility permission (System Settings → Privacy & Security → Accessibility)"),
            ("not_installed", "", "Grok isn't installed on this Mac"),
            ("not_running", "", "Grok isn't running on this Mac and didn't start"),
            ("not_signed_in", "", "Grok on this Mac isn't signed in"),
            ("bot_not_found", "", "Grok on this Mac has no bot named Acme Helper"),
            ("busy", "", "Grok on this Mac was still answering something else; nothing was sent"),
            ("needs_foreground", "", "Grok on this Mac answers only while it is in front; bring it forward and send again"),
            ("ui_changed", "no send button", "Grok changed its layout (ui_changed: no send button); nothing was pressed"),
            ("timeout", "", "Grok on this Mac didn't finish its reply in time"),
            ("send_failed", "the box stayed empty", "Grok on this Mac didn't take the message (the box stayed empty)"),
        ];
        for (code, detail, line) in cases {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let h = host("helper", &driver(&dir, code, &[json!({"type": "error", "code": code, "detail": detail})], 1, false), tx);
            let thread = h.start_thread(&settings(&dir));
            h.start_turn(&thread, "hi", None).unwrap();
            assert_eq!(strip(turn_events(&mut rx).await), [json!({"kind": "turn_failed", "error": line, "limit": false})], "{code}");
            h.stop().await;
        }
        let reports = appdriver::take_reports_for("helper");
        assert_eq!(reports.len(), cases.len(), "every failed send is reported");
        assert!(reports.iter().any(|body| body["error"]["code"] == "ui_changed" && body["error"]["detail"] == "no send button"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn needs_human_is_the_bots_reply_and_asks_the_server_for_its_person() {
        let dir = std::env::temp_dir().join(format!("fake-appdriver-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let ask = "Which account should I bill?";
        let lines = [json!({"type": "error", "code": "needs_human", "detail": "the bot asked a question", "text": ask})];
        let (tx, mut rx) = mpsc::unbounded_channel();
        let h = host("asker", &driver(&dir, "ask", &lines, 1, false), tx);
        let thread = h.start_thread(&settings(&dir));
        h.start_turn(&thread, "Send the invoice", None).unwrap();
        assert_eq!(
            strip(turn_events(&mut rx).await),
            [json!({"kind": "message", "text": ask, "final": true}), json!({"kind": "turn_completed", "status": "completed"})]
        );
        assert_eq!(appdriver::take_reports_for("asker"), [json!({"app": "grok", "messages": [], "error": {"code": "needs_human", "detail": "the bot asked a question", "text": ask}})]);
        h.stop().await;
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn an_interrupt_kills_the_driver() {
        let dir = std::env::temp_dir().join(format!("fake-appdriver-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let h = host("slow", &driver(&dir, "slow", &[json!({"type": "status", "state": "waiting"})], 0, true), tx);
        let thread = h.start_thread(&settings(&dir));
        let turn = h.start_turn(&thread, "hi", None).unwrap();
        let pid_file = dir.join("slow.pid");
        let started = std::time::Instant::now();
        while !pid_file.is_file() && started.elapsed() < Duration::from_secs(10) {
            tokio::task::yield_now().await;
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let pid: i32 = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        h.interrupt(&turn);
        assert_eq!(strip(turn_events(&mut rx).await), [json!({"kind": "turn_completed", "status": "interrupted"})]);
        assert_ne!(unsafe { libc::kill(pid, 0) }, 0, "the driver is gone");
        h.stop().await;
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[tokio::test]
    async fn a_driver_that_closes_stdout_and_hangs_is_stopped_at_the_deadline_and_frees_the_app() {
        let dir = std::env::temp_dir().join(format!("fake-appdriver-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("mute");
        let pid_file = dir.join("mute.pid");
        crate::util::write_script(&path, &format!("#!/bin/sh\ncat > /dev/null\nexec 1>&-\necho $$ > '{}'\nexec sleep 30\n", pid_file.display()));
        DEADLINE.with(|d| d.set(Some(Duration::from_secs(2))));
        // An app of its own, so the other tests here don't share its gate.
        let delivery = Delivery { app: "mute-chat".into(), bot: json!({"id": "b-9", "name": "Quiet"}) };
        let (tx, mut rx) = mpsc::unbounded_channel();
        let h = AppDriverHost::new("mute", command(&path, &delivery), tx);
        h.start();
        let thread = h.start_thread(&settings(&dir));
        let started = std::time::Instant::now();
        h.start_turn(&thread, "hi", None).unwrap();
        assert_eq!(
            strip(turn_events(&mut rx).await),
            [json!({"kind": "diagnostic", "end_reason": "Timeout: tico-appdriver harness execution limit (2 seconds)"}),
             json!({"kind": "turn_failed", "error": "Mute-chat on this Mac didn't finish its reply in time", "exit_code": -15, "process_exit": true, "limit": false})]
        );
        assert!(started.elapsed() < Duration::from_secs(8), "stopped at the deadline, not the driver's 30 s");
        let pid: i32 = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        assert_ne!(unsafe { libc::kill(pid, 0) }, 0, "the driver is reaped");
        assert!(appdriver::gate("mute-chat").try_lock_owned().is_ok(), "the app's gate is free again");
        assert_eq!(appdriver::take_reports_for("mute").len(), 1, "the timeout is reported");
        DEADLINE.with(|d| d.set(None));
        h.stop().await;
        std::fs::remove_dir_all(dir).unwrap();
    }
}
