//! A bot's one optional fallback harness (service.py configured_fallback, FALLBACK_RUNTIME): when its runtime is
//! out of quota or cannot sign in, the turn runs again there on a fresh thread. A bot out of quota that names no
//! fallback runs the turn once on another provider signed in here (service.py default_fallback, #504).

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::runner::Runner;
use crate::util::text_of;

#[derive(Clone, Debug, PartialEq)]
pub struct Hop {
    pub harness: String,
    pub runtime: String,
    pub model: String,
    pub reasoning_effort: Option<String>,
    /// Chosen from the company's providers, not named by the bot.
    pub default: bool,
}

/// The bot's fallback, or None to fail: both a harness and a model are required.
pub fn configured(config: &Value) -> Option<Hop> {
    let fallback = config.get("fallback").filter(|f| f.is_object())?;
    let harness = text_of(&fallback["harness"]).trim().to_string();
    let model = text_of(&fallback["model"]).trim().to_string();
    if harness.is_empty() || model.is_empty() {
        return None;
    }
    let effort = [&fallback["reasoning_effort"], &fallback["effort"], &config["reasoning_effort"]]
        .into_iter()
        .map(text_of)
        .find(|e| !e.is_empty());
    Some(Hop { runtime: crate::harness_tools::runtime_of(&harness), harness, model, reasoning_effort: effort, default: false })
}

/// Where a turn goes when the bot's own subscription hit its limit and it names no fallback (service.py
/// default_fallback): the first of the company's providers (`choices`, GET /api/v2/config `fallback_models`) on
/// another runtime `ready` says is signed in here. None when the bot named a fallback or turned it off
/// (`fallback: {off: true}`), or no other runtime is ready. A harness signed in by the bot's own key is
/// skipped unless the turn's `env` carries that key.
pub fn default_choice(config: &Value, choices: &[Value], env: &crate::env::Env, ready: impl Fn(&str) -> bool) -> Option<Hop> {
    let fallback = &config["fallback"];
    if fallback.is_object() && (crate::util::truthy(fallback.get("off")) || crate::util::truthy(fallback.get("harness"))) {
        return None;
    }
    let primary = text_of(&config["runtime"]);
    choices.iter().find_map(|row| {
        let (runtime, model) = (text_of(&row["runtime"]), text_of(&row["model"]));
        if runtime.is_empty() || model.is_empty() || runtime == primary || !ready(&runtime) {
            return None;
        }
        let harness = Some(text_of(&row["harness"])).filter(|h| !h.is_empty()).unwrap_or_else(|| runtime.clone());
        if !bot_key(&harness).iter().all(|k| env.get(k).is_some_and(|v| !v.trim().is_empty())) {
            return None;
        }
        let runtime = crate::harness_tools::runtime_of(&harness);
        let effort = Some(text_of(&config["reasoning_effort"])).filter(|e| !e.is_empty());
        Some(Hop { harness, runtime, model, reasoning_effort: effort, default: true })
    })
}

/// The key a harness signs in with from the bot's own grant (`api_keys = "bot"`, its preferred `api_key_env`), not a
/// login on the computer.
fn bot_key(harness: &str) -> Vec<String> {
    let runtime = crate::harness_tools::runtime_of(harness);
    if crate::harness_tools::caps(&runtime, harness).api_keys != "bot" {
        return vec![];
    }
    crate::harness_tools::api_key_env(&runtime).into_iter().take(1).collect()
}

/// The turn's note as it moves (service.py): why the primary stopped, and where the turn went.
pub fn diagnostic(primary: &str, hop: &Hop, hit_limit: bool) -> String {
    let why = if hit_limit { "hit its usage limit" } else { "unavailable" };
    let auto = if hop.default { " (no fallback set; another provider signed in here)" } else { "" };
    format!("{primary} {why}; running this turn on {}{auto}", hop.harness)
}

/// What the agent is told on a fallback turn: its configured model is not doing this work, so whatever it records
/// about who did it (a review's provenance, a pull request, a task note) names the model that is.
pub fn prompt_note(primary: &str, hop: &Hop, hit_limit: bool) -> String {
    let why = if hit_limit { "hit its usage limit" } else { "was unavailable" };
    format!(
        "This turn runs on the fallback {} with model {} because {primary} {why}; your configured model is not doing this work. Wherever you record which model did it (a review's provenance, a pull request, a task note), name {}, not {primary}.",
        hop.harness, hop.model, hop.model
    )
}

/// The company's fallback models from the last GET /api/v2/config, and when they were read. Empty from an older
/// server or when the owner turned automatic fallback off (Settings > AI providers).
static CHOICES: Mutex<Option<(Instant, Vec<Value>)>> = Mutex::new(None);

/// Keep `fallback_models` from a GET /api/v2/config answer (the harness relay reads it once a minute).
pub fn remember(config: &Value) {
    let rows = config["fallback_models"].as_array().map(|r| r.iter().filter(|v| v.is_object()).cloned().collect()).unwrap_or_default();
    *CHOICES.lock().unwrap() = Some((Instant::now(), rows));
}

impl Runner {
    /// The company's fallback models, read at most once a minute (service.py default_fallbacks).
    pub async fn default_fallbacks(&self) -> Vec<Value> {
        let fresh = CHOICES.lock().unwrap().as_ref().filter(|(at, _)| at.elapsed() < Duration::from_secs(60)).map(|(_, rows)| rows.clone());
        if let Some(rows) = fresh {
            return rows;
        }
        if let Ok(answer) = self.client.get("config").await {
            remember(&answer);
        }
        CHOICES.lock().unwrap().as_ref().map(|(_, rows)| rows.clone()).unwrap_or_default()
    }

    /// Whether `runtime` can take a turn here now (service.py fallback_ready): installed, signed in (on `profile`
    /// when the turn runs on one), not refused, and not turned off here by the owner. Read from the last readiness
    /// report, never probed mid-turn. A runtime signed in only by a key in the environment is left out: a turn's
    /// environment carries only its own runtime's keys. A runtime that turns a key into a login needs none.
    pub fn fallback_ready(&self, runtime: &str, profile: Option<&crate::profiles::Profile>) -> bool {
        if crate::harness_tools::shared().is_some_and(|t| t.lock().unwrap().chosen_off(runtime)) {
            return false; // the owner's "off" here holds even while a copy on PATH still answers
        }
        let mut row = self.runtime_rows.lock().unwrap().get(runtime).cloned().unwrap_or(Value::Null);
        if let Some(p) = profile {
            row = row["profiles"][&p.name].clone();
            if !row.is_object() {
                return false; // nobody has checked this subscription's sign-in for that runtime
            }
        }
        crate::util::truthy(row.get("installed"))
            && row["authenticated"] == "ready"
            && self.sign_in.rejection(runtime, profile).is_none()
            && (!crate::harness_tools::caps(runtime, "").api_key_login.is_empty() || self.sign_in.headless_secret(runtime).is_none())
    }
}

impl Hop {
    /// The bot's config with the hop's harness, runtime, model and effort in place.
    pub fn config(&self, config: &Value) -> Value {
        let mut out = config.clone();
        out["harness"] = json!(self.harness);
        out["runtime"] = json!(self.runtime);
        out["model"] = json!(self.model);
        out["reasoning_effort"] = self.reasoning_effort.as_ref().map_or(Value::Null, |e| json!(e));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_fallback_needs_a_harness_and_a_model() {
        assert!(configured(&json!({"fallback": {"harness": "claude"}})).is_none());
        let hop = configured(&json!({"reasoning_effort": "high", "fallback": {"harness": "antigravity", "model": "g"}})).unwrap();
        assert_eq!((hop.runtime.as_str(), hop.reasoning_effort.as_deref()), ("gemini", Some("high")));
        assert_eq!(configured(&json!({"fallback": {"harness": "claude", "model": "m", "effort": "low"}})).unwrap().reasoning_effort.as_deref(), Some("low"));
    }

    #[test]
    fn the_default_keeps_an_explicit_fallback_honours_off_and_skips_the_primary() {
        let choices = [json!({"runtime": "codex", "harness": "codex", "model": "gpt-test"}), json!({"runtime": "claude", "harness": "claude", "model": "claude-test"})];
        let config = json!({"runtime": "codex", "harness": "codex", "reasoning_effort": "high"});
        let none = crate::env::Env::new();
        let hop = default_choice(&config, &choices, &none, |_| true).unwrap();
        assert_eq!(hop, Hop { harness: "claude".into(), runtime: "claude".into(), model: "claude-test".into(), reasoning_effort: Some("high".into()), default: true });
        let mut off = config.clone();
        off["fallback"] = json!({"off": true});
        assert!(default_choice(&off, &choices, &none, |_| true).is_none());
        off["fallback"] = json!({"harness": "gemini", "model": "g"});
        assert!(default_choice(&off, &choices, &none, |_| true).is_none());
        assert!(default_choice(&config, &choices, &none, |r| r == "codex").is_none());
        assert_eq!(diagnostic("claude", &Hop { runtime: "codex".into(), harness: "codex".into(), ..hop.clone() }, true),
                   "claude hit its usage limit; running this turn on codex (no fallback set; another provider signed in here)");
        assert_eq!(diagnostic("codex", &configured(&json!({"fallback": {"harness": "claude", "model": "m"}})).unwrap(), false),
                   "codex unavailable; running this turn on claude");
        let note = prompt_note("claude", &Hop { runtime: "codex".into(), harness: "codex".into(), model: "gpt-test".into(), ..hop }, true);
        assert!(note.starts_with("This turn runs on the fallback codex with model gpt-test because claude hit its usage limit"));
        assert!(note.contains("name gpt-test, not claude"));
    }

    #[test]
    fn a_key_only_provider_is_skipped_unless_the_turn_carries_its_key() {
        let choices = [json!({"runtime": "pi", "harness": "pi", "model": "p"}), json!({"runtime": "gemini", "harness": "antigravity", "model": "g"})];
        let config = json!({"runtime": "claude", "harness": "claude"});
        let mut env = crate::env::Env::new();
        assert_eq!(default_choice(&config, &choices, &env, |_| true).unwrap().harness, "antigravity");
        env.insert("OPENROUTER_API_KEY".into(), "or-key".into());
        assert_eq!(default_choice(&config, &choices, &env, |_| true).unwrap().harness, "pi");
        assert!(default_choice(&config, &[json!({"runtime": "gemini", "model": "g"})], &env, |_| true).is_none());
    }
}
