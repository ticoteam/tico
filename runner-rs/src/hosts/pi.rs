//! The Pi coding-agent host: one `pi --mode json -p` per turn, for the models with no CLI
//! of their own, through OpenRouter. The session id is the thread id and the sessions live in the workspace:
//!
//!     pi --mode json -p --provider openrouter --model deepseek/deepseek-v4.1-flash --thinking low
//!        --tools read,bash,edit,write,grep,find,ls --session-id <uuid> --session-dir <dir> --approve
//!
//! The turn's MCP servers (the hub, the bot's streamable HTTP servers and the repository's `.mcp.json`) are its agent
//! directory's `mcp.json` (`mcp_json`), with `--tools` naming no `mcp__` entry so they stay callable. There are no
//! permission prompts (the bot also reaches Tico with the hub CLI over bash); `steer` is refused.
//! OPENROUTER_API_KEY reaches the process from the bot's granted credential.
//!
//! The Tico AI Gateway's models (a department tier such as `tico-marketing-medium-1.0`, or an old id such as
//! `tico-mid-1.0`) run on pi too, as pi's custom provider `tico` over the Gateway's OpenAI-chat endpoint: the server hands a run on one the gateway key and URL
//! (TICOAI_GATEWAY_KEY / TICOAI_GATEWAY_URL, backend/gateway_connection.py), and the turn's pi reads a `models.json`
//! naming that URL and the key's variable, never the key itself.

use std::path::PathBuf;

use serde_json::{json, Value};

use super::oneshot::{base_env, int_of, OneShot, Thread, Turn, Wire};
use super::{hub_mcp_server, Settings};
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
pub const GATEWAY_KEY_ENV: &str = "TICOAI_GATEWAY_KEY";
pub const GATEWAY_URL_ENV: &str = "TICOAI_GATEWAY_URL";
/// The turn's own directory for pi's Gateway configuration, set with the relay (gateway_relay.rs `turn_dir`).
pub const GATEWAY_DIR_ENV: &str = "TICOAI_GATEWAY_DIR";
const GATEWAY_DEFAULT_URL: &str = "https://updates.tico.team/v1/gateway";
/// The context window every Gateway model is told (`gateway_models_json_for`).
const GATEWAY_CONTEXT_WINDOW: i64 = 200_000;
/// pi's own `compaction.reserveTokens`, the least room it keeps, and the least a compaction point is given: Claude's
/// autocompact floor (hosts/claude.rs AUTOCOMPACT_RANGE).
const PI_RESERVE_DEFAULT: i64 = 16_384;
const PI_COMPACT_FLOOR: i64 = 100_000;
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
/// here: `gateway_models_json_for` adds images for a model the catalog says reads them. They reason, so pi sends the bot's effort as `reasoning_effort`; pi drops it for a model not marked `reasoning`. The Gateway
/// takes none, minimal, low, medium, high and xhigh, so pi's `max` (`effort_for`) goes as `xhigh`.
pub fn gateway_models_json(url: &str) -> Value {
    gateway_models_json_for(url, None, &Value::Null)
}

/// What pi is told a Gateway model reads: text, and images when the server's `gateway_inputs` (the catalog's `inputs`
/// for that model, backend/models_preset.py `with_gateway_inputs`) lists `image` for `id`. PDFs are not a pi input and
/// are left out. A model the server says nothing about (an older server) is text only.
fn gateway_input_for(inputs: &Value, id: &str) -> Vec<&'static str> {
    let image = inputs[id].as_array().is_some_and(|kinds| kinds.iter().any(|k| k.as_str() == Some("image")));
    if image { vec!["text", "image"] } else { vec!["text"] }
}

/// `gateway_models_json` that also names `model`, the one this turn asks for, when it is not among the old ids: the
/// Gateway's tiers and versions are not a list the runner is built with (backend/gateway_tiers.py), and pi runs only
/// a model its `models.json` names. `inputs` is the turn's `gateway_inputs` ({model id: what it reads}), Null when the
/// server sent none.
pub fn gateway_models_json_for(url: &str, model: Option<&str>, inputs: &Value) -> Value {
    let url = url.trim().trim_end_matches('/');
    let asked = model.map(str::trim).filter(|m| m.starts_with("tico-") && !GATEWAY_MODELS.iter().any(|(id, _)| id == m));
    let models: Vec<Value> = GATEWAY_MODELS
        .iter()
        .map(|(id, name)| (*id, *name))
        .chain(asked.map(|m| (m, m)))
        .map(|(id, name)| json!({"id": id, "name": name, "input": gateway_input_for(inputs, id), "reasoning": true,
                                 "thinkingLevelMap": {"max": "xhigh"}, "contextWindow": GATEWAY_CONTEXT_WINDOW, "maxTokens": 32000}))
        .collect();
    json!({"providers": {GATEWAY_PROVIDER: {
        "baseUrl": if url.is_empty() { GATEWAY_DEFAULT_URL } else { url },
        "api": "openai-completions",
        "apiKey": format!("${GATEWAY_KEY_ENV}"),
        "models": models,
    }}})
}

/// The agent directory a pi turn reads its configuration from: the turn's own (GATEWAY_DIR_ENV, removed with its relay),
/// else `<workspace>/runtime/pi-agent/<bot>`. Every turn's holds its `mcp.json` (`mcp_json`); a Gateway turn's holds
/// `models.json` and `settings.json` too (`gateway_models_json_for`, the turn's own pi settings).
pub(crate) fn gateway_agent_dir(settings: &Settings, env: &Env) -> Option<PathBuf> {
    let models = gateway_models_json_for(env.get(GATEWAY_URL_ENV).map(String::as_str).unwrap_or_default(), settings.model.as_deref(), &settings.gateway_inputs);
    let settings_json = json!({"retry": {"enabled": false}, "shellCommandPrefix": format!("unset {GATEWAY_KEY_ENV}")});
    let settings_json = with_compaction(settings_json, Some(GATEWAY_CONTEXT_WINDOW), &settings.compact_at);
    write_agent_dir(settings, env, json!({"models.json": models, "settings.json": settings_json}))
}

/// The agent directory of a turn on another provider: its `mcp.json`, and a `settings.json` that says only what its
/// model's compaction needs (`{}` where the model's window is not known, so a stale one is cleared).
fn agent_dir(settings: &Settings, env: &Env) -> Option<PathBuf> {
    let window = openrouter_context_window(&model_for(settings.model.as_deref()));
    write_agent_dir(settings, env, json!({"settings.json": with_compaction(json!({}), window, &settings.compact_at)}))
}

/// The context window of an OpenRouter model, where Tico knows it. None for every model for now (nothing in the runner
/// or the catalog states one): such a turn keeps pi's own compaction.reserveTokens.
fn openrouter_context_window(_model: &str) -> Option<i64> {
    None
}

/// The room pi keeps below a model's `window` (its `compaction.reserveTokens`, pi compacting once a session passes
/// `window - reserveTokens`) so that it compacts at `compact_at`, the bot's Compact at. Kept between pi's default and a
/// compaction point of PI_COMPACT_FLOOR: a Compact at past the window is the window's own edge, not pi's default.
pub fn reserve_tokens(window: i64, compact_at: &Value) -> i64 {
    let ceil = (window - PI_COMPACT_FLOOR).max(PI_RESERVE_DEFAULT);
    (window - super::compact_at(compact_at)).clamp(PI_RESERVE_DEFAULT, ceil)
}

/// `settings` (pi's settings.json) with the compaction `compact_at` needs for a model of `window` tokens; pi's own when
/// the window is not known.
fn with_compaction(mut settings: Value, window: Option<i64>, compact_at: &Value) -> Value {
    if let Some(window) = window {
        settings["compaction"] = json!({"reserveTokens": reserve_tokens(window, compact_at)});
    }
    settings
}

/// Writes `files` (name -> JSON) and the turn's `mcp.json` into the turn's agent directory; the directory's path, or None.
fn write_agent_dir(settings: &Settings, env: &Env, mut files: Value) -> Option<PathBuf> {
    let root = env.get("HUB_WORKSPACE").filter(|w| !w.is_empty()).cloned().unwrap_or_else(|| if settings.cwd.is_empty() { ".".into() } else { settings.cwd.clone() });
    let bot = if settings.slug.is_empty() { "pi" } else { settings.slug.as_str() };
    let dir = env.get(GATEWAY_DIR_ENV).filter(|d| !d.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(root).join("runtime").join("pi-agent").join(bot));
    files["mcp.json"] = mcp_json(settings, env);
    // The agent directory can be changed by the turn. Prepare it as that unprivileged user;
    // no privileged open or write may follow a link planted by a harness or a sibling. Isolated (-I): with no HOME,
    // Python finds the passwd home, the shared one, and would run a sibling's user site as the turn user.
    let mut child = crate::isolation::std_command_as(settings.identity, crate::env::python())
        .env_clear()
        .args(["-I", "-c", r#"import json,os,sys,secrets
root=sys.argv[1]
os.makedirs(root,mode=0o700,exist_ok=True)
body=json.load(sys.stdin)
for name,value in body.items():
 final=os.path.join(root,name)
 tmp=os.path.join(root,f".{name}.{os.getpid()}.{secrets.token_hex(8)}")
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as out: json.dump(value,out)
 os.replace(tmp,final)
"#, &dir.display().to_string()])
        .stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
        .spawn().ok()?;
    {
        use std::io::Write;
        let body = serde_json::to_vec(&files).ok()?;
        child.stdin.take()?.write_all(&body).ok()?;
    }
    if !child.wait().ok()?.success() { return None; }
    Some(dir)
}

/// pi's `mcp.json` for a turn: the same servers Claude Code is given (hosts/claude.rs `argv`), in pi's format. The
/// repository's own `.mcp.json` comes first, then the bot's declared servers, then the hub, each replacing the one before
/// by name. An SSE server (a repository's, not the bot's: those are filtered by the manifest) is left out, since pi
/// refuses it. Secrets stay `${VAR}`: the hub's variables are named there and pi reads them from the turn's environment.
pub fn mcp_json(settings: &Settings, env: &Env) -> Value {
    let project = if settings.cwd.is_empty() { None } else { std::fs::read_to_string(std::path::Path::new(&settings.cwd).join(".mcp.json")).ok() };
    let project = project
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|v| v.get("mcpServers").and_then(Value::as_object).cloned())
        .unwrap_or_default();
    let mut servers: serde_json::Map<String, Value> = project
        .into_iter()
        .filter(|(_, s)| s.is_object() && s["type"] != "sse")
        .map(|(name, mut s)| {
            s["exposure"] = json!("direct");
            (name, s)
        })
        .collect();
    servers.extend(crate::mcp::pi_config(&settings.mcp_servers));
    if let Some(hub) = hub_mcp_server(env) {
        let vars: serde_json::Map<String, Value> = hub["env"].as_object().into_iter().flatten().map(|(k, _)| (k.clone(), json!(format!("${{{k}}}")))).collect();
        servers.insert("hub".into(), json!({"command": hub["command"], "args": hub["args"], "env": vars, "exposure": "direct"}));
    }
    // pi starts a stdio server with the whole process environment, so the Gateway key (hidden from bash by
    // settings.json), the model keys and the hub token would reach it. An empty value in the server's own env
    // overrides what it inherits. A server's own env may still name a model key or the hub token (a declared server
    // can need one), but never the Gateway key; the hub server keeps its `${HUB_TOKEN}`.
    for (name, row) in servers.iter_mut() {
        if row.get("command").is_some() {
            let mut own = row["env"].as_object().cloned().unwrap_or_default();
            for key in MCP_BLANKED_ENV.iter().copied().filter(|k| *k != "HUB_TOKEN" || name.as_str() != "hub") {
                if !own.contains_key(key) {
                    own.insert(key.into(), json!(""));
                }
            }
            own.insert(GATEWAY_KEY_ENV.into(), json!(""));
            row["env"] = Value::Object(own);
        }
    }
    json!({"mcpServers": servers})
}

/// The variables a stdio MCP server is started without: the model provider keys a pi turn may hold, and the hub's token
/// (every server but the hub, which gets it through `${HUB_TOKEN}`). The Gateway key is blanked separately, always.
const MCP_BLANKED_ENV: &[&str] = &["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "HUB_TOKEN"];

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
    ("out_of_credit", "this company is out of credit"),
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
/// that isn't the Gateway's as it came. The cap still reads as a spend limit, `rate_limited` as a rate limit,
/// `out_of_credit` by its code (a limit, so the turn goes to its fallback) and a refused key as an invalid API key, so
/// the runner's limit and sign-in rules (hosts/mod.rs) classify them; the server
/// (backend/execution.py) decides from the code what is retried, when.
pub fn plain_error(text: &str) -> String {
    let Some(code) = gateway_code(text) else { return text.to_string() };
    match code {
        "cap_reached" => "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)".into(),
        // One plain message for both: a person can't tell a revoked key from a mistyped one, and needn't.
        "revoked_key" | "unknown_key" => format!("Tico AI Gateway: key not recognised (api key invalid: {code})"),
        "rate_limited" => "Tico AI Gateway: rate limited; retrying after the Gateway's requested wait (rate_limited)".into(),
        // The company's credit is spent (Tico HQ's billing): bots run on their fallbacks and are back within a minute of a credit.
        "out_of_credit" => {
            "Tico models are paused: this company is out of credit. Your bots use their other models until credit is added (out_of_credit)".into()
        }
        "unknown_model" => "Tico AI Gateway: this model isn't offered; Settings > Gateway lists the picks (unknown_model)".into(),
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
    // A leased turn user writes here through the shared group; the supervisor's umask left it owner-writable only,
    // and earlier threads' session files may be owner-only too.
    if settings.identity.is_some() {
        if let Err(e) = crate::turn_users::share_workspace_blocking(std::slice::from_ref(&path)) { crate::util::log(&format!("Tico runner: {e}")); }
    }
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
        match agent_dir(settings, &env) {
            Some(dir) => {
                env.insert("PI_CODING_AGENT_DIR".into(), dir.display().to_string());
            }
            None => crate::util::log("Tico runner: pi's agent folder could not be written, so this turn has no MCP servers"),
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
                    // Each message is one request, so the newest one's prompt and reply are the session's size.
                    let usage = usage_increment(&done["usage"]);
                    turn.context = Some(int_of(&usage["input"]) + int_of(&usage["output"]));
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
            // pi compacted the session (threshold, overflow or /compact); an aborted or failed one has no result.
            "compaction_end" if truthy(m.get("result")) => turn.compacted = true,
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
            json!({"kind": "thread_confirmed"}),
            json!({"kind": "turn_failed", "error": "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)", "limit": true}),
            json!({"kind": "status", "state": "idle"}),
        ]);
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert!(args.lines().last().unwrap().starts_with("--mode json -p --provider tico --model tico-mid-1.0 --thinking high"), "{args}");
        // pi's models.json names the Gateway and the key's variable, never the key; pi itself never retries.
        let written = std::fs::read_to_string(dir.join("runtime/pi-agent/acme/models.json")).unwrap();
        assert!(!written.contains("tgw-synthetic-fixture"));
        let models: Value = serde_json::from_str(&written).unwrap();
        assert_eq!(models["providers"]["tico"]["baseUrl"], "https://gw.example/v1/gateway");
        assert_eq!(models["providers"]["tico"]["apiKey"], "$TICOAI_GATEWAY_KEY");
        assert_eq!(models["providers"]["tico"]["api"], "openai-completions");
        let settings: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("runtime/pi-agent/acme/settings.json")).unwrap()).unwrap();
        assert_eq!(settings["retry"]["enabled"], false);
        // Every bash command pi runs starts without the key in its environment.
        assert_eq!(settings["shellCommandPrefix"], "unset TICOAI_GATEWAY_KEY");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_company_out_of_credit_reads_plainly_and_is_a_limit_so_the_bot_falls_back() {
        // Tico HQ's billing stop (Waitlist spec v3 test 12): 429 out_of_credit, type insufficient_quota, Retry-After 60.
        let (events, dir) = gateway_turn(&[
            SESSION,
            r#"{"type":"agent_start"}"#,
            r#"{"type":"message_end","message":{"role":"assistant","provider":"tico","model":"tico-mid-1.0","content":[],"stopReason":"error","usage":{"input":0,"output":0,"totalTokens":0},"errorMessage":"429: {\"code\":\"out_of_credit\",\"message\":\"Tico models are paused: this company is out of credit.\",\"type\":\"insufficient_quota\",\"param\":null}"}}"#,
            r#"{"type":"turn_end","message":{"role":"assistant","content":[]}}"#,
            r#"{"type":"agent_end"}"#,
        ]).await;
        // A person reads a sentence, not the Gateway's JSON; `limit` sends the turn to the bot's fallback.
        assert_eq!(events, vec![
            json!({"kind": "status", "state": "active"}),
            json!({"kind": "thread_confirmed"}),
            json!({"kind": "turn_failed", "error": "Tico models are paused: this company is out of credit. Your bots use their other models until credit is added (out_of_credit)", "limit": true}),
            json!({"kind": "status", "state": "idle"}),
        ]);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[tokio::test]
    async fn a_bot_still_on_retired_cheap_1_0_keeps_running_on_the_gateway() {
        let (_, dir) = gateway_turn_on("tico-cheap-1.0", &[SESSION, r#"{"type":"agent_start"}"#, r#"{"type":"agent_end"}"#]).await;
        let args = std::fs::read_to_string(dir.join("args.log")).unwrap();
        assert!(args.lines().last().unwrap().starts_with("--mode json -p --provider tico --model tico-cheap-1.0 "), "{args}");
        let models: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("runtime/pi-agent/acme/models.json")).unwrap()).unwrap();
        let ids: Vec<&str> = models["providers"]["tico"]["models"].as_array().unwrap().iter().map(|m| m["id"].as_str().unwrap()).collect();
        assert!(ids.contains(&"tico-cheap-1.0") && ids.contains(&"tico-cheap-1.1"), "{ids:?}");
        // What a person is told to pick names only the current lineup.
        assert!(!plain_error(r#"400: {"code":"unknown_model"}"#).contains("Cheap 1.0"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_pi_turn_gets_the_hub_and_the_declared_and_repository_servers_as_direct_http_servers() {
        let dir = std::env::temp_dir().join(format!("pi-mcp-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".mcp.json"), r#"{"mcpServers": {"repo": {"command": "npx", "args": ["x"]}, "legacy": {"type": "sse", "url": "https://old.example/sse"}, "hub": {"command": "stale"}}}"#).unwrap();
        let ws = dir.display().to_string();
        let mut s = Settings { cwd: ws.clone(), model: Some("kimi-k3".into()), slug: "acme".into(), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HUB_WORKSPACE", ws.as_str()), ("HUB_API_URL", "http://hub.invalid"), ("HUB_TOKEN", "hub-token-synthetic"),
                       ("HUB_BOT", "acme"), ("OPENROUTER_API_KEY", "or-synthetic"), ("LINEAR_API_KEY", "linear-synthetic")] {
            s.env.insert(k.into(), v.into());
        }
        s.mcp_servers = vec![json!({"name": "linear", "url": "https://mcp.linear.app/mcp", "transport": "http", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}})];
        let env = Pi::env(&s);
        let agent = PathBuf::from(&env["PI_CODING_AGENT_DIR"]);
        assert_eq!(agent, dir.join("runtime/pi-agent/acme"));
        let written = std::fs::read_to_string(agent.join("mcp.json")).unwrap();
        // Secrets stay `${VAR}` in the file: no granted value is written into it.
        for secret in ["hub-token-synthetic", "linear-synthetic", "or-synthetic"] {
            assert!(!written.contains(secret), "{secret} in mcp.json");
        }
        let config: Value = serde_json::from_str(&written).unwrap();
        let servers = &config["mcpServers"];
        assert_eq!(servers["linear"], json!({"url": "https://mcp.linear.app/mcp", "exposure": "direct", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}}));
        assert_eq!(servers["repo"], json!({"command": "npx", "args": ["x"], "exposure": "direct", "env": {"OPENROUTER_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "XAI_API_KEY": "", "GEMINI_API_KEY": "", "HUB_TOKEN": "", "TICOAI_GATEWAY_KEY": ""}}));
        assert!(servers.get("legacy").is_none(), "an SSE server of the repository is left out");
        assert_eq!(servers["hub"]["command"], crate::hosts::hub_mcp_server(&s.env).unwrap()["command"]);
        assert_eq!((&servers["hub"]["env"]["HUB_TOKEN"], &servers["hub"]["exposure"]), (&json!("${HUB_TOKEN}"), &json!("direct")));
        // Every stdio server's environment names the Gateway key empty, so pi's inherited copy never reaches it.
        for stdio in ["repo", "hub"] {
            assert_eq!(servers[stdio]["env"][GATEWAY_KEY_ENV], json!(""), "{stdio}");
        }
        // The turn's `--tools` names no `mcp__` entry, so pi keeps every MCP tool declared to the model.
        assert!(!TOOLS.split(',').any(|t| t.starts_with("mcp__")));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_stdio_mcp_server_starts_without_the_model_keys_or_hub_token_unless_its_own_env_names_them() {
        let dir = std::env::temp_dir().join(format!("pi-mcp-env-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(".mcp.json"), r#"{"mcpServers": {"repo": {"command": "npx", "args": ["x"]}, "tool": {"command": "uvx", "args": ["y"], "env": {"ANTHROPIC_API_KEY": "anthropic-synthetic", "TICOAI_GATEWAY_KEY": "gateway-synthetic"}}}}"#).unwrap();
        let ws = dir.display().to_string();
        let mut s = Settings { cwd: ws.clone(), model: Some("kimi-k3".into()), slug: "acme".into(), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HUB_WORKSPACE", ws.as_str()), ("HUB_API_URL", "http://hub.invalid"), ("HUB_TOKEN", "hub-token-synthetic"),
                       ("HUB_BOT", "acme"), ("OPENROUTER_API_KEY", "or-synthetic"), (GATEWAY_KEY_ENV, "gateway-synthetic")] {
            s.env.insert(k.into(), v.into());
        }
        let env = s.env.clone();
        let config = mcp_json(&s, &env);
        let servers = &config["mcpServers"];
        // A repository server that names nothing gets every blanked variable empty, the Gateway key included.
        assert_eq!(servers["repo"]["env"], json!({"OPENROUTER_API_KEY": "", "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "XAI_API_KEY": "", "GEMINI_API_KEY": "", "HUB_TOKEN": "", "TICOAI_GATEWAY_KEY": ""}));
        // Its own env keeps the one it names, but never the Gateway key.
        assert_eq!(servers["tool"]["env"], json!({"OPENROUTER_API_KEY": "", "ANTHROPIC_API_KEY": "anthropic-synthetic", "OPENAI_API_KEY": "", "XAI_API_KEY": "", "GEMINI_API_KEY": "", "HUB_TOKEN": "", "TICOAI_GATEWAY_KEY": ""}));
        // The hub keeps its token through `${HUB_TOKEN}`, and the model keys stay blank for it too.
        assert_eq!(servers["hub"]["env"]["HUB_TOKEN"], json!("${HUB_TOKEN}"));
        assert_eq!(servers["hub"]["env"]["OPENROUTER_API_KEY"], json!(""));
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
            (429, "rate_limited", "Too many refused keys from this address. Try again later.", "Tico AI Gateway: rate limited; retrying after the Gateway's requested wait (rate_limited)"),
            (429, "out_of_credit", "Tico models are paused: this company is out of credit.", "Tico models are paused: this company is out of credit. Your bots use their other models until credit is added (out_of_credit)"),
            (400, "unknown_model", "Unknown model. Tico models: tico-cheap-1.0, tico-mid-1.0, tico-power-1.0.", "Tico AI Gateway: this model isn't offered; Settings > Gateway lists the picks (unknown_model)"),
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
            assert_eq!(is_limit(&said), matches!(*code, "cap_reached" | "rate_limited" | "out_of_credit"), "{code}");
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
        // A tier or version the runner was not built with is named for the turn that asks for it, once.
        let ids = |m: Option<&str>| -> Vec<String> { gateway_models_json_for("", m, &Value::Null)["providers"]["tico"]["models"].as_array().unwrap()
            .iter().map(|v| v["id"].as_str().unwrap().to_string()).collect() };
        assert!(ids(Some("tico-marketing-medium-fast-1.0")).contains(&"tico-marketing-medium-fast-1.0".to_string()));
        assert_eq!(ids(Some("tico-mid-1.0")).len(), GATEWAY_MODELS.len());
        assert_eq!(ids(Some("kimi-k3")).len(), GATEWAY_MODELS.len());
        // A Tico-model bot needs no OpenRouter grant on its computer.
        let (state, _) = sign_in(&[json!({"bot": "gw-only-bot", "config": {"runtime": "pi", "model": "tico-mid-1.0"}})]);
        assert_eq!(state, "ready");
        assert!(model_names().contains(&"tico-power-1.0".to_string()));
    }

    #[test]
    fn a_gateway_model_takes_images_only_where_the_server_says_it_reads_them() {
        let inputs_of = |models: &Value, id: &str| -> Value {
            models["providers"]["tico"]["models"].as_array().unwrap().iter().find(|m| m["id"] == id).unwrap()["input"].clone()
        };
        let sent = json!({"tico-marketing-medium-1.0": ["text", "image", "pdf"],
                          "tico-marketing-low-fast-1.0": ["text"], "tico-power-1.0": ["text", "image", "pdf"]});
        let both = ["tico-marketing-medium-1.0", "tico-marketing-low-fast-1.0"];
        let models = |ask: &str| gateway_models_json_for("", Some(ask), &sent);
        // A Claude-backed tier: text and image (a PDF is not a pi input).
        assert_eq!(inputs_of(&models(both[0]), both[0]), json!(["text", "image"]));
        // Low Fast reads text only.
        assert_eq!(inputs_of(&models(both[1]), both[1]), json!(["text"]));
        // An old id in the built-in list takes what the server says of it, and text where it says nothing.
        assert_eq!(inputs_of(&models(both[0]), "tico-power-1.0"), json!(["text", "image"]));
        assert_eq!(inputs_of(&models(both[0]), "tico-mid-1.0"), json!(["text"]));
        // No value (an older server), or one that lists no image: text only, for every model.
        for none in [Value::Null, json!({}), json!({"tico-marketing-medium-1.0": []}), json!({"tico-marketing-medium-1.0": "image"})] {
            let models = gateway_models_json_for("", Some(both[0]), &none);
            for model in models["providers"]["tico"]["models"].as_array().unwrap() {
                assert_eq!(model["input"], json!(["text"]), "{model}");
            }
        }
    }

    #[test]
    fn compact_at_is_the_windows_room_for_pi_within_its_floor_and_default() {
        // A 200,000-token window: Compact at 150,000 leaves pi 50,000 of room; a Compact at past the window is pi's default;
        // one below the floor stops at the floor. A window of another size works the same way.
        assert_eq!(reserve_tokens(200_000, &json!(150_000)), 50_000);
        assert_eq!(reserve_tokens(200_000, &json!(250_000)), PI_RESERVE_DEFAULT);
        assert_eq!(reserve_tokens(200_000, &json!(10)), 100_000);
        assert_eq!(reserve_tokens(200_000, &Value::Null), PI_RESERVE_DEFAULT);
        assert_eq!(reserve_tokens(1_000_000, &json!(600_000)), 400_000);
        assert_eq!(reserve_tokens(100_000, &json!(50_000)), PI_RESERVE_DEFAULT);
    }

    #[test]
    fn a_gateway_turn_compacts_at_compact_at_and_keeps_its_retry_and_shell_settings() {
        let dir = std::env::temp_dir().join(format!("pi-compact-gw-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let ws = dir.display().to_string();
        let mut s = Settings { cwd: ws.clone(), model: Some("tico-mid-1.0".into()), slug: "acme".into(), compact_at: json!(150_000), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HUB_WORKSPACE", ws.as_str()), (GATEWAY_KEY_ENV, "tgw-synthetic-fixture"), (GATEWAY_URL_ENV, "https://gw.example/v1/gateway")] {
            s.env.insert(k.into(), v.into());
        }
        let env = Pi::env(&s);
        let agent = PathBuf::from(&env["PI_CODING_AGENT_DIR"]);
        let settings: Value = serde_json::from_str(&std::fs::read_to_string(agent.join("settings.json")).unwrap()).unwrap();
        // The Gateway's window (200,000) less Compact at; the retry and the key-hiding shell prefix are still there.
        assert_eq!(settings["compaction"]["reserveTokens"], 50_000);
        assert_eq!(settings["retry"]["enabled"], false);
        assert_eq!(settings["shellCommandPrefix"], "unset TICOAI_GATEWAY_KEY");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn an_openrouter_turn_keeps_pis_compaction_until_its_model_has_a_known_window() {
        let dir = std::env::temp_dir().join(format!("pi-compact-or-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let ws = dir.display().to_string();
        let mut s = Settings { cwd: ws.clone(), model: Some("kimi-k3".into()), slug: "acme".into(), compact_at: json!(150_000), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin"), ("HUB_WORKSPACE", ws.as_str()), ("OPENROUTER_API_KEY", "or-synthetic")] {
            s.env.insert(k.into(), v.into());
        }
        let env = Pi::env(&s);
        let agent = PathBuf::from(&env["PI_CODING_AGENT_DIR"]);
        // No window is known for an OpenRouter model yet, so pi's own reserve stands and no Gateway setting is left over.
        assert_eq!(std::fs::read_to_string(agent.join("settings.json")).unwrap().trim(), "{}");
        // Where a window is known, the same mapping applies (a 1,000,000-token window and Compact at 600,000).
        assert_eq!(with_compaction(json!({}), Some(1_000_000), &json!(600_000))["compaction"]["reserveTokens"], 400_000);
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_compaction_with_a_result_marks_the_turn_and_one_that_failed_or_aborted_does_not() {
        let mut turn = Turn::default();
        Pi::on_event(&json!({"type": "compaction_end", "reason": "threshold", "aborted": false, "willRetry": false,
                             "result": {"summary": "Earlier work.", "tokensBefore": 180_000}}), &mut turn);
        assert!(turn.compacted);
        for end in [json!({"type": "compaction_end", "reason": "threshold", "aborted": true, "willRetry": false}),
                    json!({"type": "compaction_end", "reason": "threshold", "aborted": false, "errorMessage": "Summary failed", "willRetry": false})] {
            let mut turn = Turn::default();
            Pi::on_event(&end, &mut turn);
            assert!(!turn.compacted, "{end}");
            assert!(turn.error.is_empty() && turn.out.is_empty(), "{end}");
        }
    }

    #[test]
    fn the_newest_requests_prompt_and_reply_are_the_sessions_context() {
        let mut turn = Turn::default();
        Pi::on_event(&json!({"type": "message_end", "message": {"role": "assistant", "provider": "openrouter", "stopReason": "stop", "content": [],
                             "usage": {"input": 120, "output": 9, "cacheRead": 40, "cacheWrite": 0}}}), &mut turn);
        assert_eq!(turn.context, Some(169));
    }

    #[tokio::test]
    async fn a_turn_that_compacts_the_session_is_recorded_for_its_thread_once() {
        let dir = std::env::temp_dir().join(format!("fake-pi-compact-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = fake(&dir, "pi");
        let fixture = dir.join("compact.jsonl");
        std::fs::write(&fixture, [SESSION, r#"{"type":"agent_start"}"#, r#"{"type":"compaction_start","reason":"threshold"}"#,
                                  r#"{"type":"compaction_end","reason":"threshold","aborted":false,"willRetry":false,"result":{"summary":"Earlier work.","tokensBefore":180000}}"#,
                                  r#"{"type":"agent_end"}"#].join("\n")).unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        let host = PiHost::new("acme", vec![cmd], tx);
        host.start();
        let mut s = Settings { cwd: dir.display().to_string(), model: Some("kimi-k3".into()), slug: "acme".into(), ..Default::default() };
        for (k, v) in [("PATH", "/usr/bin:/bin".to_string()), ("HUB_WORKSPACE", dir.display().to_string()), ("OPENROUTER_API_KEY", "or-synthetic".into()),
                       ("ARGS_LOG", dir.join("args.log").display().to_string()), ("FIXTURE", fixture.display().to_string()), ("RC", "0".into())] {
            s.env.insert(k.into(), v);
        }
        let thread = host.start_thread(&s);
        host.start_turn(&thread, "hello", None).unwrap();
        turn_events(&mut rx).await;
        host.stop().await;
        // The turn that compacted is told once, and a turn with no assistant message says nothing about the context.
        assert!(host.compacted(&thread));
        assert!(!host.compacted(&thread));
        assert_eq!(host.context_tokens(&thread), None);
        std::fs::remove_dir_all(dir).unwrap();
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
