//! A bot's one optional fallback harness (service.py configured_fallback, FALLBACK_RUNTIME): when its runtime is
//! out of quota or cannot sign in, the turn runs again there on a fresh thread.

use serde_json::{json, Value};

use crate::util::text_of;

#[derive(Clone, Debug, PartialEq)]
pub struct Hop {
    pub harness: String,
    pub runtime: String,
    pub model: String,
    pub reasoning_effort: Option<String>,
}

fn runtime_of(harness: &str) -> String {
    match harness {
        "antigravity" => "gemini".into(),
        other => other.into(),
    }
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
    Some(Hop { runtime: runtime_of(&harness), harness, model, reasoning_effort: effort })
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
}
