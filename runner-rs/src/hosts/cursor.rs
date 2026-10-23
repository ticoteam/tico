//! The Cursor agent host: one `cursor-agent -p --output-format stream-json` per turn. A
//! thread is a chat made with `cursor-agent create-chat`, whose id is the thread id, so a restart resumes it:
//!
//!     cursor-agent -p --output-format stream-json --force --trust --model auto --workspace <repo> --resume <chat id>
//!
//! The prompt goes in on stdin. No effort setting (it lives in the model name), no steering, and no MCP: the
//! bot uses the hub CLI over bash. CURSOR_API_KEY, when set, signs it in without a browser login.

use std::process::Stdio;
use std::sync::OnceLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Value};

use super::oneshot::{base_env, int_of, OneShot, Thread, Turn, Wire};
use super::{failed, HostError, Settings};
use crate::env::Env;
use crate::util::{text_of, truthy};

pub const EXECUTABLE: &str = "cursor-agent";
/// Catalog id -> the CLI's own model id; anything else the person names passes through.
pub const MODELS: &[(&str, &str)] = &[("cursor-auto", "auto")];
const DEFAULT_MODEL: &str = "auto";
const CREATE_TIMEOUT: Duration = Duration::from_secs(30);

pub fn model_names() -> Vec<String> {
    let mut names: Vec<String> = MODELS.iter().map(|(k, _)| k.to_string()).collect();
    names.sort();
    names
}

pub fn model_for(model: Option<&str>) -> String {
    let m = model.unwrap_or_default().trim();
    if m.is_empty() || m == "default" {
        return DEFAULT_MODEL.into();
    }
    MODELS.iter().find(|(k, _)| *k == m).map_or(m, |(_, v)| v).to_string()
}

/// (input, output, total) from a result's `usage`: cache reads and writes count as input.
pub fn usage_tokens(usage: &Value) -> (i64, i64, i64) {
    let inp = int_of(&usage["inputTokens"]) + int_of(&usage["cacheReadTokens"]) + int_of(&usage["cacheWriteTokens"]);
    let out = int_of(&usage["outputTokens"]);
    (inp, out, inp + out)
}

fn message_text(message: &Value) -> String {
    match &message["content"] {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => blocks.iter().filter(|b| b["type"] == "text").map(|b| text_of(&b["text"])).collect(),
        _ => String::new(),
    }
}

fn chat_id() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}").unwrap())
}

pub struct Cursor;

impl Wire for Cursor {
    const NAME: &'static str = "cursor";
    const PROGRAM: &'static str = EXECUTABLE;

    fn argv(cmd: &[String], thread_id: &str, t: &Thread, _effort: Option<&str>) -> Vec<String> {
        let mut argv = cmd.to_vec();
        argv.extend(
            ["-p", "--output-format", "stream-json", "--force", "--trust", "--model", &model_for(t.settings.model.as_deref()), "--workspace", &t.settings.cwd, "--resume", thread_id]
                .map(String::from),
        );
        argv
    }

    fn env(settings: &Settings) -> Env {
        let mut env = base_env(settings);
        env.extend(crate::harness_tools::env_for(Self::NAME));
        env
    }

    fn on_event(msg: &Value, turn: &mut Turn) {
        if !msg.is_object() {
            return;
        }
        match msg["type"].as_str().unwrap_or_default() {
            "assistant" => {
                let text = message_text(&msg["message"]);
                if !text.is_empty() {
                    turn.reply = text.clone();
                    if !turn.extra["texts"].is_array() {
                        turn.extra["texts"] = json!([]);
                    }
                    turn.extra["texts"].as_array_mut().unwrap().push(json!(text));
                    turn.emit("message", json!({"text": text, "final": false}));
                }
            }
            "tool_call" if msg["subtype"] == "started" => {
                let name = msg["tool_call"].as_object().and_then(|c| c.keys().next().cloned()).unwrap_or_default();
                turn.emit("tool", json!({"name": name, "tool": name, "phase": "tool_execution_start"}));
            }
            "result" => {
                turn.ended = true;
                let subtype_ok = matches!(msg.get("subtype"), None | Some(Value::Null)) || msg["subtype"] == "success";
                if truthy(msg.get("is_error")) || !subtype_ok {
                    let why = ["result", "error", "subtype"].iter().map(|k| &msg[*k]).find(|v| truthy(Some(v))).map(text_of);
                    turn.error = why.unwrap_or_else(|| "cursor error".into());
                } else if truthy(msg.get("result")) {
                    turn.reply = text_of(&msg["result"]);
                    // Cursor's answer runs the turn's messages together with nothing between them: each keeps its
                    // own paragraph, as Claude's separate answers do.
                    let texts: Vec<&str> = turn.extra["texts"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
                    if texts.len() > 1 && texts.concat() == turn.reply {
                        turn.reply = texts.join("\n\n");
                    }
                }
                let (inp, out, total) = usage_tokens(&msg["usage"]);
                if total != 0 {
                    let cached = int_of(&msg["usage"]["cacheReadTokens"]);
                    turn.emit("tokens", json!({"input": inp, "output": out, "total": total, "usage": {"input": inp, "cached": cached, "output": out}}));
                }
            }
            _ => {}
        }
    }

    fn succeeded(rc: i32, turn: &Turn) -> bool {
        turn.ended && turn.error.is_empty() && rc == 0
    }
}

pub type CursorHost = OneShot<Cursor>;

impl CursorHost {
    /// A new chat (`cursor-agent create-chat`): its id is the thread id.
    async fn new_chat(&self, settings: &Settings) -> Result<String, HostError> {
        let cmd = self.cmd();
        let mut command = crate::isolation::command(&cmd[0]);
        command.args(&cmd[1..]).arg("create-chat").current_dir(&settings.cwd).env_clear().envs(Cursor::env(settings)).stdin(Stdio::null()).kill_on_drop(true);
        let out = match tokio::time::timeout(CREATE_TIMEOUT, command.output()).await {
            Err(_) => return Err(failed(format!("could not start a cursor chat: create-chat timed out after {}s", CREATE_TIMEOUT.as_secs()))),
            Ok(Err(e)) => return Err(failed(format!("could not start a cursor chat: {e}"))),
            Ok(Ok(out)) => out,
        };
        let stdout = String::from_utf8_lossy(&out.stdout);
        match chat_id().find(&stdout) {
            Some(found) if out.status.success() => Ok(found.as_str().into()),
            _ => {
                let stderr = String::from_utf8_lossy(&out.stderr);
                let text = if stderr.is_empty() { stdout.trim().to_string() } else { stderr.trim().to_string() };
                let tail: String = text.chars().skip(text.chars().count().saturating_sub(300)).collect();
                Err(failed(format!("cursor-agent create-chat failed: {}", if tail.is_empty() { "no chat id".into() } else { tail })))
            }
        }
    }

    pub async fn start_thread(&self, settings: &Settings) -> Result<String, HostError> {
        let id = self.new_chat(settings).await?;
        Ok(self.record(&id, settings, true))
    }

    pub fn resume_thread(&self, thread_id: &str, settings: &Settings) -> String {
        self.record(thread_id, settings, true)
    }
}

#[cfg(test)]
mod tests {
    use super::super::oneshot::testing::{fake, turn_events};
    use super::*;
    use tokio::sync::mpsc;

    #[tokio::test]
    async fn every_cursor_fixture_gives_the_python_hosts_events_on_a_new_chat() {
        let dir = std::env::temp_dir().join(format!("fake-cursor-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake(&dir, "cursor-agent");
        for (path, expected) in crate::hosts::fixtures::cases("cursor") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = CursorHost::new("acme", vec![cmd.clone()], tx);
            host.start();
            let mut s = Settings { cwd: dir.display().to_string(), model: Some("cursor-auto".into()), ..Default::default() };
            for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", path.display().to_string())] {
                s.env.insert(k.into(), v);
            }
            let thread = host.start_thread(&s).await.unwrap();
            assert_eq!(thread, "0b5c2f1e-3a4d-4e6f-8a9b-0c1d2e3f4a5b");
            host.start_turn(&thread, "hello", Some("high")).unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
        }
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert_eq!(args.lines().next().unwrap(), "create-chat");
        assert_eq!(
            args.lines().nth(1).unwrap(),
            format!("-p --output-format stream-json --force --trust --model auto --workspace {} --resume 0b5c2f1e-3a4d-4e6f-8a9b-0c1d2e3f4a5b", dir.display())
        );
        assert_eq!(std::fs::read_to_string(dir.join("args.log.prompt")).unwrap(), "hello");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn models_and_usage_follow_the_python_host() {
        assert_eq!((model_for(None), model_for(Some("default")), model_for(Some("cursor-auto")), model_for(Some("gpt-5.5"))), ("auto".into(), "auto".into(), "auto".into(), "gpt-5.5".into()));
        assert_eq!(usage_tokens(&json!({"inputTokens": 10, "cacheReadTokens": 4, "cacheWriteTokens": 1, "outputTokens": 2})), (15, 2, 17));
    }
}
