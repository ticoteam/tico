//! The Pi coding-agent host: one `pi --mode json -p` per turn, for the models with no CLI
//! of their own, through OpenRouter. The session id is the thread id and the sessions live in the workspace:
//!
//!     pi --mode json -p --provider openrouter --model deepseek/deepseek-v4.1-flash --thinking low
//!        --tools read,bash,edit,write,grep,find,ls --session-id <uuid> --session-dir <dir> --approve
//!
//! Pi has no MCP and no permission prompts (the bot uses the hub CLI over bash); `steer` is refused.
//! OPENROUTER_API_KEY reaches the process from the bot's granted credential.
//!
//! The Tico AI Gateway's models (`tico-cheap-1.1`, `tico-mid-1.0`, `tico-power-1.0`) run on pi too, as pi's custom
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
pub const GATEWAY_MODELS: &[(&str, &str)] = &[
    ("tico-cheap-1.1", "Tico Cheap 1.1"),
    ("tico-mid-1.0", "Tico Mid 1.0"),
    ("tico-power-1.0", "Tico Power 1.0"),
    ("tico-cheap-1.0", "Tico Cheap 1.0"),
];
/// Retired from the lineup (backend/providers.py `deprecated`): still run for the bots already on them, never offered.
const GATEWAY_RETIRED: &[&str] = &["tico-cheap-1.0"];
pub const GATEWAY_KEY_ENV: &str = "TICOAI_GATEWAY_KEY";
pub const GATEWAY_URL_ENV: &str = "TICOAI_GATEWAY_URL";
/// The turn's own directory for pi's Gateway configuration, set with the relay (gateway_relay.rs `turn_dir`).
pub const GATEWAY_DIR_ENV: &str = "TICOAI_GATEWAY_DIR";
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

/// pi's `models.json` for the Gateway: its OpenAI-chat endpoint at `url` (the runner's relay, gateway_relay.rs), the
/// token read from GATEWAY_KEY_ENV when pi makes a request. It holds no secret. The models take text and tools only
/// (the Gateway refuses images). They reason, so pi sends the bot's effort as `reasoning_effort`; pi drops it for a model not marked `reasoning`. The Gateway
/// takes none, minimal, low, medium, high and xhigh, so pi's `max` (`effort_for`) goes as `xhigh`.
pub fn gateway_models_json(url: &str) -> Value {
    let url = url.trim().trim_end_matches('/');
    let models: Vec<Value> = GATEWAY_MODELS
        .iter()
        .map(|(id, name)| json!({"id": id, "name": name, "input": ["text"], "reasoning": true,
                                 "thinkingLevelMap": {"max": "xhigh"}, "contextWindow": 200000, "maxTokens": 32000}))
        .collect();
    json!({"providers": {GATEWAY_PROVIDER: {
        "baseUrl": if url.is_empty() { GATEWAY_DEFAULT_URL } else { url },
        "api": "openai-completions",
        "apiKey": format!("${GATEWAY_KEY_ENV}"),
        "models": models,
    }}})
}

/// The agent directory a Gateway turn's pi reads its `models.json` from: the turn's own (GATEWAY_DIR_ENV, removed with
/// its relay), else `<workspace>/runtime/pi-gateway`.
pub(crate) fn gateway_agent_dir(settings: &Settings, env: &Env) -> Option<PathBuf> {
    let root = env.get("HUB_WORKSPACE").filter(|w| !w.is_empty()).cloned().unwrap_or_else(|| if settings.cwd.is_empty() { ".".into() } else { settings.cwd.clone() });
    let dir = env.get(GATEWAY_DIR_ENV).filter(|d| !d.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(root).join("runtime").join("pi-gateway"));
    crate::isolation::mkdir(&dir, 0o755).ok()?;
    let body = serde_json::to_vec_pretty(&gateway_models_json(env.get(GATEWAY_URL_ENV).map(String::as_str).unwrap_or_default())).ok()?;
    std::fs::write(dir.join("models.json"), body).ok()?;
    crate::isolation::chown(&dir.join("models.json"));
    // pi retries any 429 or 5xx within seconds, and never waits for Retry-After. The Gateway's `rate_limited` asks for
    // an hour (more refused keys from this address extend it), so a Gateway turn does not retry in pi: the server
    // retries by the Gateway's code instead (backend/execution.py).
    // pi's bash tool hands its commands pi's whole environment: each command starts by unsetting GATEWAY_KEY_ENV. It
    // holds only the turn's token for the runner's relay, never the Gateway's key (gateway_relay.rs); a command can
    // still read pi's /proc environ, so this only keeps the token out of ordinary tool output.
    let settings_json = json!({"retry": {"enabled": false}, "shellCommandPrefix": format!("unset {GATEWAY_KEY_ENV}")});
    std::fs::write(dir.join("settings.json"), serde_json::to_vec_pretty(&settings_json).ok()?).ok()?;
    crate::isolation::chown(&dir.join("settings.json"));
    Some(dir)
}

/// Loopback hosts, added to NO_PROXY for a Gateway turn. pi sends every request through the environment's proxy
/// (a CONNECT tunnel, even for plain http), so a proxy without a loopback bypass would carry a plain-http local
/// Gateway's request, key and all, and reach its own loopback, not this computer's.
const LOOPBACK: &[&str] = &["localhost", "127.0.0.1", "::1"];

fn bypass_proxy_for_loopback(env: &mut Env) {
    for name in ["NO_PROXY", "no_proxy"] {
        let mut hosts: Vec<String> =
            env.get(name).map(|v| v.split(',').map(|h| h.trim().to_string()).filter(|h| !h.is_empty()).collect()).unwrap_or_default();
        for host in LOOPBACK {
            if !hosts.iter().any(|h| h.eq_ignore_ascii_case(host)) {
                hosts.push(host.to_string());
            }
        }
        env.insert(name.into(), hosts.join(","));
    }
}

/// The Tico AI Gateway's error codes (the same on every endpoint), each with the fixed message it is sent with. pi
/// prints a refusal as `<status>: <the error object>`, code included, but an error inside a stream only by its message,
/// so either names it.
const GATEWAY_ERRORS: &[(&str, &str)] = &[
    ("cap_reached", "monthly cap is reached"),
    ("revoked_key", "gateway key was revoked"),
    ("unknown_key", "unknown gateway key"),
    ("rate_limited", "too many refused keys"),
    ("unknown_model", "unknown model. tico models"),
    ("unsupported_field", "take text and tools only"),
    ("bad_request", "not a chat completion the gateway can send"),
    ("upstream_refused", "refused this request as it was sent"),
    ("no_route", "no route for this model"),
    ("upstream_unavailable", "the model is unavailable"),
];

/// The Gateway's code in a model error pi printed, if it is one of the Gateway's.
pub fn gateway_code(text: &str) -> Option<&'static str> {
    let lower = text.to_lowercase();
    let squeezed: String = lower.chars().filter(|c| !c.is_whitespace()).collect();
    GATEWAY_ERRORS.iter().find(|(code, _)| squeezed.contains(&format!("\"code\":\"{code}\""))).map(|(code, _)| *code).or_else(|| {
        GATEWAY_ERRORS.iter().find(|(code, said)| lower.contains(code) || lower.contains(said)).map(|(code, _)| *code)
    })
    .or_else(|| lower.contains("gateway cap reached").then_some("cap_reached"))
}

/// The Gateway's own message in a refusal pi printed as `<status>: {"code": .., "message": ..}`, if it has one.
fn gateway_said(text: &str) -> Option<String> {
    let body = &text[text.find('{')?..];
    let error: Value = serde_json::from_str(body).ok()?;
    let error = if error["error"].is_object() { &error["error"] } else { &error };
    error["message"].as_str().map(|m| m.trim().chars().take(200).collect()).filter(|m: &String| !m.is_empty())
}

/// A model error of a turn on the Tico AI Gateway in words a person reads, ending with the Gateway's code; anything
/// that isn't the Gateway's as it came. The cap still reads as a spend limit, `rate_limited` as a rate limit and a
/// refused key as an invalid API key, so the runner's limit and sign-in rules (hosts/mod.rs) classify them; the server
/// (backend/execution.py) decides from the code what is retried, when.
pub fn plain_error(text: &str) -> String {
    let Some(code) = gateway_code(text) else { return text.to_string() };
    match code {
        "cap_reached" => "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)".into(),
        // One plain message for both: a person can't tell a revoked key from a mistyped one, and needn't.
        "revoked_key" | "unknown_key" => format!("Tico AI Gateway: key not recognised (api key invalid: {code})"),
        "rate_limited" => "Tico AI Gateway: rate limited after too many refused keys from this address; wait an hour (rate_limited)".into(),
        "unknown_model" => {
            let names: Vec<&str> =
                GATEWAY_MODELS.iter().filter(|(id, _)| !GATEWAY_RETIRED.contains(id)).map(|(_, name)| *name).collect();
            format!("Tico AI Gateway: this model isn't offered. Tico models: {} (unknown_model)", names.join(", "))
        }
        "no_route" => "Tico AI Gateway: no Tico route for this request (no_route)".into(),
        "upstream_unavailable" => "Tico AI Gateway is unavailable, try again (upstream_unavailable)".into(),
        _ => {
            let said = gateway_said(text).unwrap_or_else(|| "the request can't be sent as it is".into());
            format!("Tico AI Gateway refused the request as sent: {said} ({code})")
        }
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
    let out = first(usage, OUTPUT);
    // One assistant message is one model request, so the server prices a long prompt on its own (Tico Cheap 1.1).
    json!({"input": inp, "cached": read, "output": out, "requests": [[inp - read, read, out]]})
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
            bypass_proxy_for_loopback(&mut env);
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
                let gateway = done["provider"] == GATEWAY_PROVIDER;
                if gateway {
                    turn.extra["gateway"] = json!(true);
                }
                let failed = done["role"] == "assistant" && done["stopReason"] == "error" && truthy(done.get("errorMessage"));
                let partial = if failed { assistant_text(done) } else { String::new() };
                if failed && gateway && !partial.trim().is_empty() {
                    // The Gateway's stream broke after it began: the reply keeps what came, and says it was cut off.
                    let said = plain_error(&text_of(&done["errorMessage"]));
                    turn.reply = format!("{}\n\n(The Tico AI Gateway cut this reply off: {said})", partial.trim_end());
                    turn.emit("message", json!({"text": turn.reply, "final": false}));
                    turn.extra["cut_off"] = json!(true);
                    return;
                }
                if failed {
                    // A model call that failed (the Gateway's cap, a refused key): pi ends the message with its error.
                    let text = text_of(&done["errorMessage"]);
                    turn.error = if gateway { plain_error(&text) } else { text };
                    turn.extra["model_error"] = json!(true);
                }
                // A refused call used nothing: no zero count, so a turn that did nothing else still reads as having done nothing.
                let nothing = failed && gateway && usage_tokens(&done["usage"]).2 == 0;
                if done["role"] == "assistant" && done["usage"].is_object() && !nothing {
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
                if !text.is_empty() && !truthy(turn.extra.get("cut_off")) {
                    turn.reply = text;
                }
            }
            "error" => {
                let err = [m.get("error"), m.get("message")].into_iter().flatten().find(|v| truthy(Some(v))).cloned().unwrap_or(json!("pi error"));
                let text = match &err {
                    Value::Object(e) => e.get("message").filter(|v| truthy(Some(v))).map(text_of).unwrap_or_else(|| err.to_string()),
                    other => text_of(other),
                };
                turn.error = if truthy(turn.extra.get("gateway")) { plain_error(&text) } else { text };
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

    /// The events of one turn of a Tico-model bot whose fake pi prints `lines`, and the turn's workspace.
    async fn gateway_turn(lines: &[&str]) -> (Vec<Value>, PathBuf) {
        gateway_turn_on("tico-mid-1.0", lines).await
    }

    async fn gateway_turn_on(model: &str, lines: &[&str]) -> (Vec<Value>, PathBuf) {
        let dir = std::env::temp_dir().join(format!("fake-pi-gw-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake(&dir, "pi");
        let fixture = dir.join("gateway.jsonl");
        std::fs::write(&fixture, lines.join("\n")).unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = PiHost::new("acme", vec![cmd.clone()], tx);
        host.start();
        let mut s = Settings { cwd: dir.display().to_string(), model: Some(model.into()), slug: "acme".into(), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("HUB_WORKSPACE", dir.display().to_string()), (GATEWAY_KEY_ENV, "tgw-synthetic-fixture".into()), (GATEWAY_URL_ENV, "https://gw.example/v1/gateway/".into()), ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", fixture.display().to_string()), ("RC", "0".into())] {
            s.env.insert(k.into(), v);
        }
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hello", Some("high")).unwrap();
        let events = turn_events(&mut rx).await;
        host.stop().await;
        (events, dir)
    }

    const SESSION: &str = r#"{"type":"session","version":3,"id":"0b5c2f1e-3a4d-4e6f-8a9b-0c1d2e3f4a5b"}"#;

    #[tokio::test]
    async fn a_tico_model_runs_on_the_gateway_and_its_cap_reads_as_gateway_cap_reached() {
        // pi ends a refused model call with the error on the assistant message (`<status>: <error object>`), then exits 0.
        let (events, dir) = gateway_turn(&[
            SESSION,
            r#"{"type":"agent_start"}"#,
            r#"{"type":"message_end","message":{"role":"assistant","provider":"tico","model":"tico-mid-1.0","content":[],"stopReason":"error","usage":{"input":0,"output":0,"totalTokens":0},"errorMessage":"429: {\"code\":\"cap_reached\",\"message\":\"The Tico AI Gateway's monthly cap is reached.\",\"type\":\"insufficient_quota\",\"param\":null}"}}"#,
            r#"{"type":"turn_end","message":{"role":"assistant","content":[]}}"#,
            r#"{"type":"agent_end"}"#,
        ]).await;
        // No zero token count: a refused call did nothing, so the server may treat the turn as having done nothing.
        assert_eq!(events, vec![
            json!({"kind": "status", "state": "active"}),
            json!({"kind": "turn_failed", "error": "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)", "limit": true}),
            json!({"kind": "status", "state": "idle"}),
        ]);
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert!(args.lines().last().unwrap().starts_with("--mode json -p --provider tico --model tico-mid-1.0 --thinking high"), "{args}");
        // pi's models.json names the Gateway and the key's variable, never the key; pi itself never retries.
        let written = std::fs::read_to_string(dir.join("runtime/pi-gateway/models.json")).unwrap();
        assert!(!written.contains("tgw-synthetic-fixture"));
        let models: Value = serde_json::from_str(&written).unwrap();
        assert_eq!(models["providers"]["tico"]["baseUrl"], "https://gw.example/v1/gateway");
        assert_eq!(models["providers"]["tico"]["apiKey"], "$TICOAI_GATEWAY_KEY");
        assert_eq!(models["providers"]["tico"]["api"], "openai-completions");
        let settings: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("runtime/pi-gateway/settings.json")).unwrap()).unwrap();
        assert_eq!(settings["retry"]["enabled"], false);
        // Every bash command pi runs starts without the key in its environment.
        assert_eq!(settings["shellCommandPrefix"], "unset TICOAI_GATEWAY_KEY");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_bot_still_on_retired_cheap_1_0_keeps_running_on_the_gateway() {
        let (_, dir) = gateway_turn_on("tico-cheap-1.0", &[SESSION, r#"{"type":"agent_start"}"#, r#"{"type":"agent_end"}"#]).await;
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert!(args.lines().last().unwrap().starts_with("--mode json -p --provider tico --model tico-cheap-1.0 "), "{args}");
        let models: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("runtime/pi-gateway/models.json")).unwrap()).unwrap();
        let ids: Vec<&str> = models["providers"]["tico"]["models"].as_array().unwrap().iter().map(|m| m["id"].as_str().unwrap()).collect();
        assert!(ids.contains(&"tico-cheap-1.0") && ids.contains(&"tico-cheap-1.1"), "{ids:?}");
        // What a person is told to pick names only the current lineup.
        assert!(!plain_error(r#"400: {"code":"unknown_model"}"#).contains("Cheap 1.0"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn each_assistant_message_is_one_priced_request_and_they_add_up_to_the_totals() {
        let mut meter = crate::execute::Meter::default();
        for usage in [json!({"input": 5_000, "cacheRead": 115_000, "output": 2_000}), json!({"input": 1_000, "cacheRead": 50_000, "output": 800})] {
            meter.add(&json!({"usage": usage_increment(&usage)}));
        }
        let report = meter.report("tico-mid-1.0", "pi", "api").unwrap();
        assert_eq!((report["input_tokens"].clone(), report["cached_tokens"].clone(), report["output_tokens"].clone()), (json!(6_000), json!(165_000), json!(2_800)));
        assert_eq!(report["requests"], json!([[5_000, 115_000, 2_000], [1_000, 50_000, 800]]));
    }

    #[test]
    fn a_gateway_turn_never_sends_a_loopback_request_through_a_proxy() {
        let dir = std::env::temp_dir().join(format!("pi-proxy-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ws = dir.display().to_string();
        let mut s = Settings { cwd: ws.clone(), model: Some("tico-mid-1.0".into()), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HUB_WORKSPACE", ws.as_str()), (GATEWAY_KEY_ENV, "tgw-synthetic-fixture"),
                       (GATEWAY_URL_ENV, "http://127.0.0.1:8080/v1/gateway"), ("HTTP_PROXY", "http://proxy.example:3128"),
                       ("NO_PROXY", "corp.example, LOCALHOST")] {
            s.env.insert(k.into(), v.to_string());
        }
        let env = Pi::env(&s);
        // The proxy stays for everything else; loopback is added once to both spellings, beside what was there.
        assert_eq!(env["HTTP_PROXY"], "http://proxy.example:3128");
        assert_eq!(env["NO_PROXY"], "corp.example,LOCALHOST,127.0.0.1,::1");
        assert_eq!(env["no_proxy"], "localhost,127.0.0.1,::1");
        // A turn on another provider's model is left as it was.
        s.model = Some("kimi-k3".into());
        let other = Pi::env(&s);
        assert_eq!(other["NO_PROXY"], "corp.example, LOCALHOST");
        assert!(!other.contains_key("no_proxy"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_gateway_stream_that_breaks_keeps_its_partial_reply_and_says_so() {
        // The Gateway sent two chunks, then `data: {"error": ...}` and no [DONE]: pi ends the message with what came.
        let (events, dir) = gateway_turn(&[
            SESSION,
            r#"{"type":"agent_start"}"#,
            r#"{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hello there, "}}"#,
            r#"{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"friend"}}"#,
            r#"{"type":"message_end","message":{"role":"assistant","provider":"tico","model":"tico-mid-1.0","content":[{"type":"text","text":"Hello there, friend"}],"stopReason":"error","errorMessage":"The model is unavailable. Try again later."}}"#,
            r#"{"type":"turn_end","message":{"role":"assistant","content":[{"type":"text","text":"Hello there, friend"}]}}"#,
            r#"{"type":"agent_end"}"#,
        ]).await;
        std::fs::remove_dir_all(dir).unwrap();
        let said = "Hello there, friend\n\n(The Tico AI Gateway cut this reply off: Tico AI Gateway is unavailable, try again (upstream_unavailable))";
        assert!(!events.iter().any(|e| e["kind"] == "turn_failed"), "{events:?}");
        assert!(events.contains(&json!({"kind": "message", "text": said, "final": true})), "{events:?}");
        assert!(events.contains(&json!({"kind": "turn_completed", "status": "completed"})), "{events:?}");
    }

    #[test]
    fn every_gateway_code_reads_plainly_and_keeps_its_classification() {
        use crate::hosts::{is_auth_rejected, is_limit};
        // As pi prints a refusal (`<status>: <the error object>`) and an error inside a stream (its message only).
        let refusal = |status: u16, code: &str, message: &str| {
            format!("{status}: {}", json!({"code": code, "message": message, "type": "x", "param": null}))
        };
        let table: &[(u16, &str, &str, &str)] = &[
            (429, "cap_reached", "The Tico AI Gateway's monthly cap is reached.", "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)"),
            (401, "revoked_key", "This gateway key was revoked.", "Tico AI Gateway: key not recognised (api key invalid: revoked_key)"),
            (401, "unknown_key", "Unknown gateway key.", "Tico AI Gateway: key not recognised (api key invalid: unknown_key)"),
            (429, "rate_limited", "Too many refused keys from this address. Try again later.", "Tico AI Gateway: rate limited after too many refused keys from this address; wait an hour (rate_limited)"),
            (400, "unknown_model", "Unknown model. Tico models: tico-cheap-1.0, tico-mid-1.0, tico-power-1.0.", "Tico AI Gateway: this model isn't offered. Tico models: Tico Cheap 1.1, Tico Mid 1.0, Tico Power 1.0 (unknown_model)"),
            (400, "unsupported_field", "Tico models here take text and tools only: no images, audio, files or more than one choice.", "Tico AI Gateway refused the request as sent: Tico models here take text and tools only: no images, audio, files or more than one choice. (unsupported_field)"),
            (400, "bad_request", "The request is not a chat completion the gateway can send.", "Tico AI Gateway refused the request as sent: The request is not a chat completion the gateway can send. (bad_request)"),
            (400, "upstream_refused", "The model refused this request as it was sent.", "Tico AI Gateway refused the request as sent: The model refused this request as it was sent. (upstream_refused)"),
            (503, "no_route", "No route for this model meets the request's filters.", "Tico AI Gateway: no Tico route for this request (no_route)"),
            (503, "upstream_unavailable", "The model is unavailable. Try again later.", "Tico AI Gateway is unavailable, try again (upstream_unavailable)"),
        ];
        for (status, code, message, plain) in table {
            let said = plain_error(&refusal(*status, code, message));
            assert_eq!(&said, plain, "{code}");
            assert_eq!(gateway_code(message), Some(*code), "{code} inside a stream");
            // What the runner and server classify by: a cap or rate limit is a limit, a refused key a refused sign-in,
            // and nothing else is either.
            assert_eq!(is_limit(&said), matches!(*code, "cap_reached" | "rate_limited"), "{code}");
            assert_eq!(is_auth_rejected(&said), matches!(*code, "revoked_key" | "unknown_key"), "{code}");
            assert!(!crate::hosts::rejection_reason(&said).contains("[redacted]"), "{code}");
        }
        // Not the Gateway's: as it came.
        assert_eq!(plain_error("429 rate limit: slow down"), "429 rate limit: slow down");
        assert_eq!(gateway_models_json("")["providers"]["tico"]["baseUrl"], GATEWAY_DEFAULT_URL);
        // pi sends `reasoning_effort` only for a model marked `reasoning` (checked against pi 1.3.0 and a local fake
        // chat endpoint: low, high and max went as low, high and xhigh; unmarked, none went); text in, as the Gateway.
        for model in gateway_models_json("")["providers"]["tico"]["models"].as_array().unwrap() {
            assert_eq!((&model["reasoning"], &model["thinkingLevelMap"]["max"], &model["input"]),
                       (&json!(true), &json!("xhigh"), &json!(["text"])), "{model}");
        }
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
        assert_eq!(usage_increment(&json!({"input": 5, "cacheRead": 4, "cacheWrite": 1, "output": 2})), json!({"input": 10, "cached": 4, "output": 2, "requests": [[6, 4, 2]]}));
        let mut env = Env::new();
        assert_eq!(openrouter_key(&env, false), "");
        env.insert("OPENROUTER_API_KEY".into(), " k ".into());
        assert_eq!(openrouter_key(&env, false), "k");
        assert_eq!(model_names()[0], "deepseek-v4-pro");
    }
}
