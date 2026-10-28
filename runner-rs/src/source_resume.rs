//! Quota and capacity fallback sessions never replace the conversation's primary provider session.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Saved {
    pub thread_id: String,
    pub runtime: String,
    pub harness: String,
    pub model: String,
    pub reasoning_effort: String,
    pub profile: String,
    pub epoch: Option<(String, i64)>,
}

pub fn scope(attempt: &Value, profile: Option<&crate::profiles::Profile>, worktree: bool) -> String {
    let mut key = format!("source:{}", crate::util::text_of(&attempt["job_id"]));
    if let Some(profile) = profile {
        key.push_str(&format!(":profile:{}:{}", profile.name, profile.binding()));
    }
    if worktree {
        key.push_str(":worktree");
    }
    key
}

pub fn apply(attempt: &mut Value) -> Option<Saved> {
    let saved: Saved = serde_json::from_value(attempt.get("source_continuation")?.clone()).ok()?;
    let config = attempt["config"].as_object_mut()?;
    config.insert("runtime".into(), json!(saved.runtime));
    config.insert("harness".into(), json!(saved.harness));
    config.insert("model".into(), json!(saved.model));
    config.insert("reasoning_effort".into(), json!(saved.reasoning_effort));
    config.insert("fallback".into(), json!({"off": true}));
    config.remove("primary_held");
    config.remove("task_model_fallback");
    attempt["continue_thread"] = json!(saved.thread_id);
    Some(saved)
}
