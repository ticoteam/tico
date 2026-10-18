//! The Pi coding-agent host: one `pi --mode json -p` per turn, for the models with no CLI
//! of their own, through OpenRouter. The session id is the thread id and the sessions live in the workspace:
//!
//!     pi --mode json -p --provider openrouter --model deepseek/deepseek-v4.1-flash --thinking low
//!        --tools read,bash,edit,write,grep,find,ls --session-id <uuid> --session-dir <dir> --approve
//!
//! Pi has no MCP and no permission prompts (the bot uses the hub CLI over bash); `steer` is refused.
//! OPENROUTER_API_KEY reaches the process from the bot's granted credential.
//!
//! The Tico AI Gateway's models (`tico-cheap-1.0`, `tico-mid-1.0`, `tico-power-1.0`) run on pi too, as pi's custom
//! provider `tico` over the Gateway's OpenAI-chat endpoint: the server hands a run on one the gateway key and URL
//! (TICOAI_GATEWAY_KEY / TICOAI_GATEWAY_URL, backend/gateway_connection.py), and the turn's pi reads a `models.json`
//! naming that URL and the key's variable, never the key itself.

use std::path::PathBuf;

use serde_json::{json, Value};

use super::oneshot::{base_env, int_of, OneShot, Thread, Turn, Wire};
use super::Settings;
use crate::env::Env;
use crate::util::{text_of, truthy};

pub const PROVIDER: &str = "openrouter";
/// Catalog id -> OpenRouter model id (clients/openrouter.py MODELS, shared with the server's decision model).
pub const MODELS: &[(&str, &str)] = &[
    ("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
    ("deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
    ("kimi-k3", "moonshotai/kimi-k3"),
    ("kimi-k2.7-code", "moonshotai/kimi-k2.7-code"),
    ("llama-4-maverick", "meta-llama/llama-4-maverick"),
    ("mistral-medium-3.5", "mistralai/mistral-medium-3-5"),
    ("devstral-2512", "mistralai/devstral-2512"),
];
const DEFAULT_MODEL: &str = "deepseek-v4.1-flash";
/// The Tico AI Gateway's chat models: pi's custom provider `tico` (`gateway_models_json`).
pub const GATEWAY_PROVIDER: &str = "tico";
pub const GATEWAY_MODELS: &[(&str, &str)] =
    &[("tico-cheap-1.0", "Tico Cheap 1.0"), ("tico-mid-1.0", "Tico Mid 1.0"), ("tico-power-1.0", "Tico Power 1.0")];
pub const GATEWAY_KEY_ENV: &str = "TICOAI_GATEWAY_KEY";
pub const GATEWAY_URL_ENV: &str = "TICOAI_GATEWAY_URL";
const GATEWAY_DEFAULT_URL: &str = "https://updates.tico.team/v1/gateway";
const TOOLS: &str = "read,bash,edit,write,grep,find,ls";

/// The catalog ids, sorted (readiness `models`).
pub fn model_names() -> Vec<String> {
    let mut names: Vec<String> = MODELS.iter().chain(GATEWAY_MODELS).map(|(k, _)| k.to_string()).collect();
    names.sort();
    names
}

/// The OpenRouter id for a catalog id; one that is already OpenRouter's (or unknown) passes through.
pub fn model_for(model: Option<&str>) -> String {
    let m = model.unwrap_or_default().trim();
    let m = if m.is_empty() || m == "default" { DEFAULT_MODEL } else { m };
    MODELS.iter().find(|(k, _)| *k == m).map_or(m, |(_, v)| v).to_string()
}

/// Whether `model` is one of the Tico AI Gateway's, run as pi's `tico` provider.
pub fn is_gateway(model: Option<&str>) -> bool {
    model.is_some_and(|m| m.trim().starts_with("tico-"))
}

/// pi's `models.json` for the Gateway: its OpenAI-chat endpoint at `url`, the key read from GATEWAY_KEY_ENV when pi
/// makes a request. It holds no secret.
pub fn gateway_models_json(url: &str) -> Value {
    let url = url.trim().trim_end_matches('/');
    let models: Vec<Value> = GATEWAY_MODELS
        .iter()
        .map(|(id, name)| json!({"id": id, "name": name, "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 32000}))
        .collect();
    json!({"providers": {GATEWAY_PROVIDER: {
        "baseUrl": if url.is_empty() { GATEWAY_DEFAULT_URL } else { url },
        "api": "openai-completions",
        "apiKey": format!("${GATEWAY_KEY_ENV}"),
        "models": models,
    }}})
}

/// The agent directory a Gateway turn's pi reads its `models.json` from: `<workspace>/runtime/pi-gateway`.
fn gateway_agent_dir(settings: &Settings, env: &Env) -> Option<PathBuf> {
    let root = env.get("HUB_WORKSPACE").filter(|w| !w.is_empty()).cloned().unwrap_or_else(|| if settings.cwd.is_empty() { ".".into() } else { settings.cwd.clone() });
    let dir = PathBuf::from(root).join("runtime").join("pi-gateway");
    crate::isolation::mkdir(&dir, 0o755).ok()?;
    let body = serde_json::to_vec_pretty(&gateway_models_json(env.get(GATEWAY_URL_ENV).map(String::as_str).unwrap_or_default())).ok()?;
    std::fs::write(dir.join("models.json"), body).ok()?;
    crate::isolation::chown(&dir.join("models.json"));
    Some(dir)
}

/// A model error in words a person reads, for the Tico AI Gateway's refusals (its codes, or its messages as pi prints
/// them); anything else as it came. The cap still reads as a spend limit and a refused key as a revoked API key, so the
/// runner's limit and sign-in rules (hosts/mod.rs) classify them as before.
pub fn plain_error(text: &str) -> String {
    let lower = text.to_lowercase();
    if lower.contains("cap_reached") || lower.contains("gateway cap reached") {
        "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)".into()
    } else if lower.contains("key_revoked") {
        "Tico AI Gateway: api key revoked (key_revoked)".into()
    } else if lower.contains("invalid_key") {
        "Tico AI Gateway: invalid api key (invalid_key)".into()
    } else {
        text.to_string()
    }
}

pub fn effort_for(effort: Option<&str>) -> &'static str {
    match effort.unwrap_or_default().trim().to_lowercase().as_str() {
        "high" => "high",
        "xhigh" | "max" => "max",
        _ => "low",
    }
}

fn first(u: &Value, keys: &[&str]) -> i64 {
    keys.iter().map(|k| int_of(&u[*k])).find(|v| *v != 0).unwrap_or(0)
}

const INPUT: &[&str] = &["input", "input_tokens", "inputTokens", "prompt_tokens", "promptTokens"];
const OUTPUT: &[&str] = &["output", "output_tokens", "outputTokens", "completion_tokens", "completionTokens"];

/// (input, output, total) from a Pi/OpenRouter usage object.
pub fn usage_tokens(usage: &Value) -> (i64, i64, i64) {
    let (inp, out) = (first(usage, INPUT), first(usage, OUTPUT));
    let total = first(usage, &["total", "total_tokens", "totalTokens"]);
    (inp, out, if total != 0 { total } else { inp + out })
}

/// {input, cached, output} for one assistant message: Pi keeps cache reads and writes apart from `input`; the
/// meter counts them all as input, the reads also as cached.
pub fn usage_increment(usage: &Value) -> Value {
    let read = first(usage, &["cacheRead", "cache_read", "cachedTokens"]);
    let inp = first(usage, INPUT) + read + first(usage, &["cacheWrite", "cache_write"]);
    json!({"input": inp, "cached": read, "output": first(usage, OUTPUT)})
}

/// The run's granted key; outside a turn (no bot environment) also the machine's, as local diagnostics read it.
pub fn openrouter_key(env: &Env, local: bool) -> String {
    let process = if local { std::env::var("OPENROUTER_API_KEY").ok() } else { None };
    if let Some(v) = [env.get("OPENROUTER_API_KEY").cloned(), process].into_iter().flatten().find(|v| !v.trim().is_empty()) {
        return v.trim().to_string();
    }
    if !local {
        return String::new();
    }
    let root = PathBuf::from(env.get("HUB_WORKSPACE").cloned().unwrap_or_default());
    crate::signin::read_env(&root.join("secrets").join("_shared.env"))
        .get("OPENROUTER_API_KEY")
        .map(|v| v.trim_matches(|c| c == '\'' || c == '"').to_string())
        .unwrap_or_default()
}

/// The pi runtime's sign-in: an OPENROUTER_API_KEY grant for every bot that runs on it.
pub fn sign_in(assignments: &[Value]) -> (&'static str, String) {
    let mut missing: Vec<String> = assignments
        .iter()
        .filter(|r| r["config"]["runtime"] == "pi")
        // A Tico model signs in with the company's gateway key, which the server hands its run.
        .filter(|r| !is_gateway(r["config"]["model"].as_str()))
        .map(|r| text_of(&r["bot"]))
        .filter(|bot| !crate::hosts::gemini::has_grant(bot, "OPENROUTER_API_KEY"))
        .collect();
    missing.sort();
    if missing.is_empty() {
        ("ready", "OpenRouter API key configured".into())
    } else {
        ("missing", format!("OpenRouter API key grant required ({})", missing.join(", ")))
    }
}

/// `<workspace>/runtime/pi-sessions/<bot>`, made on first use (the bot user's, when isolated).
pub fn session_dir(bot: &str, settings: &Settings) -> PathBuf {
    let root = settings.env.get("HUB_WORKSPACE").filter(|w| !w.is_empty()).cloned().unwrap_or_else(|| if settings.cwd.is_empty() { ".".into() } else { settings.cwd.clone() });
    let path = PathBuf::from(root).join("runtime").join("pi-sessions").join(if bot.is_empty() { "pi" } else { bot });
    let _ = crate::isolation::mkdir(&path, 0o777);
    path
}

/// An assistant message's text: a plain string, or its text blocks joined.
fn assistant_text(message: &Value) -> String {
    match message {
        Value::String(s) if !s.trim().is_empty() => s.clone(),
        Value::Object(m) => {
            if !matches!(m.get("role"), None | Some(Value::Null)) && m.get("role") != Some(&json!("assistant")) {
                return String::new();
            }
            match m.get("content") {
                Some(Value::String(s)) => s.clone(),
                Some(Value::Array(blocks)) => blocks
                    .iter()
                    .filter_map(|b| match b {
                        Value::String(s) => Some(s.clone()),
                        Value::Object(b) if b.get("type").is_none_or(|t| t.is_null() || t == "text") && truthy(b.get("text")) => Some(text_of(&b["text"])),
                        _ => None,
                    })
                    .collect(),
                _ => String::new(),
            }
        }
        _ => String::new(),
    }
}

pub struct Pi;

impl Wire for Pi {
    const NAME: &'static str = "pi";
    const PROGRAM: &'static str = "pi";

    fn argv(cmd: &[String], thread_id: &str, t: &Thread, effort: Option<&str>) -> Vec<String> {
        let s = &t.settings;
        let mut argv = cmd.to_vec();
        let gateway = is_gateway(s.model.as_deref());
        let model = if gateway { s.model.clone().unwrap_or_default().trim().to_string() } else { model_for(s.model.as_deref()) };
        argv.extend(
            [
                "--mode", "json", "-p", "--provider", if gateway { GATEWAY_PROVIDER } else { PROVIDER }, "--model", &model,
                "--thinking", effort_for(effort.or(s.effort.as_deref())), "--tools", TOOLS, "--session-id", thread_id,
                "--session-dir", &session_dir(&t.bot, s).display().to_string(), "--approve",
            ]
            .map(String::from),
        );
        argv
    }

    fn env(settings: &Settings) -> Env {
        let mut env = base_env(settings);
        if is_gateway(settings.model.as_deref()) {
            if let Some(dir) = gateway_agent_dir(settings, &env) {
                env.insert("PI_CODING_AGENT_DIR".into(), dir.display().to_string());
            }
            return env;
        }
        let key = openrouter_key(&env, settings.env.is_empty());
        if !key.is_empty() {
            env.insert("OPENROUTER_API_KEY".into(), key);
        }
        env
    }

    fn on_event(msg: &Value, turn: &mut Turn) {
        let Some(m) = msg.as_object() else { return };
        match m.get("type").and_then(|t| t.as_str()).unwrap_or_default() {
            "session" if truthy(m.get("id")) => turn.session = true,
            "message_update" => {
                let ev = &msg["assistantMessageEvent"];
                if ev["type"] == "text_delta" && truthy(ev.get("delta")) {
                    turn.emit("delta", json!({"text": ev["delta"], "delta_kind": "text"}));
                }
                if truthy(m.get("usage")) {
                    let (inp, out, total) = usage_tokens(&msg["usage"]);
                    if total != 0 {
                        turn.emit("tokens", json!({"input": inp, "output": out, "total": total}));
                    }
                }
            }
            "message_end" => {
                let done = &msg["message"];
                if done["role"] == "assistant" && done["stopReason"] == "error" && truthy(done.get("errorMessage")) {
                    // A model call that failed (the Gateway's cap, a refused key): pi ends the message with its error.
                    turn.error = plain_error(&text_of(&done["errorMessage"]));
                    turn.extra["model_error"] = json!(true);
                }
                if done["role"] == "assistant" && done["usage"].is_object() {
                    // One assistant message ends once, so this is where its tokens are counted.
                    let (inp, out, total) = usage_tokens(&done["usage"]);
                    turn.emit("tokens", json!({"input": inp, "output": out, "total": total, "usage": usage_increment(&done["usage"])}));
                }
                let text = assistant_text(done);
                if !text.is_empty() {
                    turn.reply = text.clone();
                    turn.emit("message", json!({"text": text, "final": false}));
                }
            }
            "turn_end" => {
                let text = assistant_text(&msg["message"]);
                if !text.is_empty() {
                    turn.reply = text;
                }
            }
            "error" => {
                let err = [m.get("error"), m.get("message")].into_iter().flatten().find(|v| truthy(Some(v))).cloned().unwrap_or(json!("pi error"));
                turn.error = plain_error(&match &err {
                    Value::Object(e) => e.get("message").filter(|v| truthy(Some(v))).map(text_of).unwrap_or_else(|| err.to_string()),
                    other => text_of(other),
                });
            }
            "agent_end" => turn.ended = true,
            kind @ ("tool_execution_start" | "tool_execution_end") => {
                let name = m.get("toolName").filter(|v| truthy(Some(v))).cloned().unwrap_or(json!(""));
                turn.emit("tool", json!({"name": name, "tool": name, "phase": kind}));
            }
            _ => {}
        }
    }

    fn succeeded(rc: i32, turn: &Turn) -> bool {
        // A model call that failed fails the turn even when pi exits 0, so a cap or a refused key is never a quiet "done".
        !truthy(turn.extra.get("model_error")) && (rc == 0 || (turn.ended && turn.error.is_empty()))
    }
}

pub type PiHost = OneShot<Pi>;

impl PiHost {
    pub fn start_thread(&self, settings: &Settings) -> String {
        self.record(&uuid::Uuid::new_v4().to_string(), settings, false)
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
    async fn every_pi_fixture_gives_the_python_hosts_events_and_its_command_line() {
        let dir = std::env::temp_dir().join(format!("fake-pi-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake(&dir, "pi");
        for (path, expected) in crate::hosts::fixtures::cases("pi") {
            let (tx, mut rx) = mpsc::unbounded_channel();
            let host = PiHost::new("acme", vec![cmd.clone()], tx);
            host.start();
            let mut s = Settings { cwd: dir.display().to_string(), model: Some("kimi-k3".into()), slug: "acme".into(), ..Default::default() };
            for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("HUB_WORKSPACE", dir.display().to_string()), ("OPENROUTER_API_KEY", " or-key ".into()), ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", path.display().to_string())] {
                s.env.insert(k.into(), v);
            }
            let rc = if path.file_stem().is_some_and(|n| n == "error") { "1" } else { "0" };
            s.env.insert("RC".into(), rc.into());
            let thread = host.start_thread(&s);
            host.start_turn(&thread, "hello", Some("xhigh")).unwrap();
            assert_eq!(turn_events(&mut rx).await, expected, "{}", path.display());
            host.stop().await;
            let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
            let sessions = dir.join("runtime/pi-sessions/acme");
            assert_eq!(
                args.lines().last().unwrap(),
                format!("--mode json -p --provider openrouter --model moonshotai/kimi-k3 --thinking max --tools {TOOLS} --session-id {thread} --session-dir {} --approve", sessions.display())
            );
            assert!(sessions.is_dir());
        }
        assert_eq!(std::fs::read_to_string(dir.join("args.log.prompt")).unwrap(), "hello");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_tico_model_runs_on_the_gateway_and_its_cap_reads_as_gateway_cap_reached() {
        let dir = std::env::temp_dir().join(format!("fake-pi-gw-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake(&dir, "pi");
        // pi ends a failed model call with the error on the assistant message, then exits 0.
        let fixture = dir.join("cap.jsonl");
        std::fs::write(&fixture, [
            r#"{"type":"session","version":3,"id":"0b5c2f1e-3a4d-4e6f-8a9b-0c1d2e3f4a5b"}"#,
            r#"{"type":"agent_start"}"#,
            r#"{"type":"message_end","message":{"role":"assistant","content":[],"stopReason":"error","errorMessage":"429 Gateway cap reached for this key this month"}}"#,
            r#"{"type":"turn_end","message":{"role":"assistant","content":[]}}"#,
            r#"{"type":"agent_end"}"#,
        ].join("\n")).unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = PiHost::new("acme", vec![cmd.clone()], tx);
        host.start();
        let mut s = Settings { cwd: dir.display().to_string(), model: Some("tico-mid-1.0".into()), slug: "acme".into(), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("HUB_WORKSPACE", dir.display().to_string()), (GATEWAY_KEY_ENV, "tgw-synthetic-fixture".into()), (GATEWAY_URL_ENV, "https://gw.example/v1/gateway/".into()), ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", fixture.display().to_string()), ("RC", "0".into())] {
            s.env.insert(k.into(), v);
        }
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hello", Some("high")).unwrap();
        assert_eq!(turn_events(&mut rx).await, vec![
            json!({"kind": "status", "state": "active"}),
            json!({"kind": "turn_failed", "error": "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)", "limit": true}),
            json!({"kind": "status", "state": "idle"}),
        ]);
        host.stop().await;
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert!(args.lines().last().unwrap().starts_with("--mode json -p --provider tico --model tico-mid-1.0 --thinking high"), "{args}");
        // pi's models.json names the Gateway and the key's variable, never the key.
        let written = std::fs::read_to_string(dir.join("runtime/pi-gateway/models.json")).unwrap();
        assert!(!written.contains("tgw-synthetic-fixture"));
        let models: Value = serde_json::from_str(&written).unwrap();
        assert_eq!(models["providers"]["tico"]["baseUrl"], "https://gw.example/v1/gateway");
        assert_eq!(models["providers"]["tico"]["apiKey"], "$TICOAI_GATEWAY_KEY");
        assert_eq!(models["providers"]["tico"]["api"], "openai-completions");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn the_gateways_refusals_read_plainly_and_keep_their_classification() {
        let cap = plain_error("429 {\"error\":{\"code\":\"cap_reached\"}}");
        assert!(cap.starts_with("Gateway cap reached") && crate::hosts::limit_re().is_match(&cap));
        let revoked = plain_error("401 key_revoked");
        assert!(crate::hosts::is_auth_rejected(&revoked), "{revoked}");
        assert!(crate::hosts::is_auth_rejected(&plain_error("401 invalid_key")));
        let rate = plain_error("429 rate_limited: slow down");
        assert_eq!(rate, "429 rate_limited: slow down");
        assert!(!rate.contains("Gateway cap"));
        assert_eq!(gateway_models_json("")["providers"]["tico"]["baseUrl"], GATEWAY_DEFAULT_URL);
        // A Tico-model bot needs no OpenRouter grant on its computer.
        let (state, _) = sign_in(&[json!({"bot": "gw-only-bot", "config": {"runtime": "pi", "model": "tico-mid-1.0"}})]);
        assert_eq!(state, "ready");
        assert!(model_names().contains(&"tico-power-1.0".to_string()));
    }

    #[test]
    fn models_efforts_usage_and_keys_follow_the_python_host() {
        assert_eq!(model_for(None), "deepseek/deepseek-v4.1-flash");
        assert_eq!(model_for(Some("default")), "deepseek/deepseek-v4.1-flash");
        assert_eq!(model_for(Some("qwen/qwen3-coder")), "qwen/qwen3-coder");
        assert_eq!((effort_for(Some("Medium")), effort_for(Some("high")), effort_for(Some("max")), effort_for(None)), ("low", "high", "max", "low"));
        assert_eq!(usage_tokens(&json!({"promptTokens": "7", "output": 0, "completion_tokens": 3})), (7, 3, 10));
        assert_eq!(usage_increment(&json!({"input": 5, "cacheRead": 4, "cacheWrite": 1, "output": 2})), json!({"input": 10, "cached": 4, "output": 2}));
        let mut env = Env::new();
        assert_eq!(openrouter_key(&env, false), "");
        env.insert("OPENROUTER_API_KEY".into(), " k ".into());
        assert_eq!(openrouter_key(&env, false), "k");
        assert_eq!(model_names()[0], "deepseek-v4-pro");
    }
}
