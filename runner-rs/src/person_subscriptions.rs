//! A person's model subscription a turn signs in with (backend/model_subscriptions.py), read with the run's own token
//! from `credential-runtime` next to its granted credentials, so it reaches whichever computer runs the turn and never
//! the bot's repository.
//!
//! An environment subscription (`env`: a Claude setup-token as CLAUDE_CODE_OAUTH_TOKEN, an Anthropic key) is set in the
//! turn's environment, and the runtime's other sign-in variables are taken out so the CLI cannot prefer one of them
//! (Claude Code ranks ANTHROPIC_API_KEY above the token). The computer's own key for that runtime is not added
//! (`Runner::turn_key`). A home subscription (`file`: a ChatGPT login or an OpenAI key, Codex's `auth.json`) is written
//! 0600 into a home of its own, `<bot state>/subscriptions/<id>/<runtime>`, kept on this computer between turns so
//! the CLI's threads resume there, and rewritten only when the server's revision moves; the turn's CODEX_HOME points
//! at it and its conversation key names it, so no thread begun on another login is resumed on it. When Codex
//! refreshed the login during the turn the new file goes back (`runner-person-subscription-refresh`) at the revision
//! the turn had, so the next computer starts from it.
//!
//! One computer is one trust group (SECURITY.md): every bot on it runs as the same OS user, so another bot's turn here
//! could read a subscription's home or a running turn's environment.

use std::collections::BTreeSet;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::env::Env;
use crate::util::text_of;

const REVISION_FILE: &str = ".tico-revision";
const MAX_VALUE: usize = 64 * 1024;

/// One subscription put in place for a turn.
#[derive(Clone, Debug, PartialEq)]
pub struct Delivered {
    pub id: String,
    pub runtime: String,
    pub kind: String,
    pub revision: i64,
    pub billing: &'static str,
    /// The CLI's own home (CODEX_HOME) for a home subscription, and what was written to its file.
    pub home: Option<PathBuf>,
    pub file: Option<PathBuf>,
    pub written: String,
}

impl Delivered {
    /// The conversation key's suffix for a home subscription: a thread lives in that home.
    pub fn binding(&self) -> Option<String> {
        self.home.as_ref().map(|_| format!(":subscription:{}", self.id))
    }
}

fn safe_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 80 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

fn billing(value: &Value) -> &'static str {
    if value.as_str() == Some("api") { "api" } else { "subscription" }
}

/// Put each subscription the server handed this turn in place: environment ones into `env` (named in `keys`, the
/// turn's vault variables, so nothing strips them), home ones under `state_dir`. Every value joins `values`, the
/// turn's redaction list. A malformed item is left out and said in the runner's log; the turn then runs as it would
/// have without it.
pub fn apply(state_dir: &Path, items: &Value, env: &mut Env, values: &mut Vec<String>, keys: &mut BTreeSet<String>)
             -> Vec<Delivered> {
    let mut out = vec![];
    for item in items.as_array().into_iter().flatten() {
        let id = text_of(&item["id"]);
        let runtime = text_of(&item["runtime"]);
        let value = text_of(&item["value"]);
        if !safe_id(&id) || runtime.is_empty() || value.is_empty() || value.len() > MAX_VALUE
            || out.iter().any(|d: &Delivered| d.runtime == runtime) {
            crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {id} is not usable"));
            continue;
        }
        values.push(value.clone());
        let mut delivered = Delivered {
            id: id.clone(), runtime: runtime.clone(), kind: text_of(&item["kind"]),
            revision: item["revision"].as_i64().unwrap_or(0), billing: billing(&item["billing"]),
            home: None, file: None, written: value.clone(),
        };
        let env_name = text_of(&item["env"]);
        if !env_name.is_empty() {
            if !crate::harness_tools::api_key_env(&runtime).contains(&env_name) {
                crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {env_name} does not sign in {runtime}"));
                values.pop();
                continue;
            }
            for other in crate::harness_tools::api_key_env(&runtime) {
                env.remove(&other);
                keys.remove(&other);
            }
            env.insert(env_name.clone(), value);
            keys.insert(env_name);
        } else {
            let name = text_of(&item["file"]);
            if name != "auth.json" {
                crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {id} names no known file"));
                values.pop();
                continue;
            }
            let home = crate::isolation::bot_state(state_dir).join("subscriptions").join(&id).join(&runtime);
            match place(&home, &name, &value, delivered.revision) {
                Ok(written) => {
                    delivered.file = Some(home.join(&name));
                    delivered.home = Some(home);
                    delivered.written = written;
                }
                Err(e) => {
                    crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {e}"));
                    values.pop();
                    continue;
                }
            }
        }
        out.push(delivered);
    }
    out
}

/// Write `value` to `home/name` unless the file there is already at `revision` (Codex may have refreshed it since, and
/// that refresh is newer than nothing the server has). Returns what the file holds now.
fn place(home: &Path, name: &str, value: &str, revision: i64) -> Result<String, String> {
    crate::isolation::mkdir(home, 0o700).map_err(|e| format!("cannot make a subscription home: {e}"))?;
    let path = home.join(name);
    let marker = home.join(REVISION_FILE);
    let at = std::fs::read_to_string(&marker).ok().and_then(|t| t.trim().parse::<i64>().ok());
    if at == Some(revision) {
        if let Ok(kept) = std::fs::read_to_string(&path) {
            return Ok(kept);
        }
    }
    write_private(&path, value)?;
    write_private(&marker, &revision.to_string())?;
    Ok(value.to_string())
}

fn write_private(path: &Path, value: &str) -> Result<(), String> {
    let staged = path.with_extension(format!("tico-{}", uuid::Uuid::new_v4().simple()));
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&staged)
        .map_err(|e| format!("cannot write a subscription file: {e}"))?;
    crate::isolation::chown_file(&f).map_err(|e| e.to_string())?;
    f.write_all(value.as_bytes()).map_err(|e| e.to_string())?;
    drop(f);
    std::fs::rename(&staged, path).map_err(|e| e.to_string())
}

/// Point each home subscription's runtime at its home in `env` (after the bot's profile set its own), and take out
/// the variables that CLI reads ahead of that home.
pub fn point_homes(delivered: &[Delivered], env: &mut Env) {
    for d in delivered {
        let Some(home) = &d.home else { continue };
        let Some(var) = crate::profiles::home_var(&d.runtime) else { continue };
        for key in crate::harness_tools::caps(&d.runtime, "").profile_home_overrides {
            env.remove(&key);
        }
        env.insert(var, home.display().to_string());
    }
}

pub fn for_runtime<'a>(delivered: &'a [Delivered], runtime: &str) -> Option<&'a Delivered> {
    delivered.iter().find(|d| d.runtime == runtime)
}

/// The refreshed file to send back for one home subscription, or None when the turn left it as written.
pub fn refreshed(d: &Delivered) -> Option<String> {
    if d.kind != "codex_login" {
        return None;
    }
    let now = std::fs::read_to_string(d.file.as_ref()?).ok()?;
    (now != d.written && !now.trim().is_empty() && now.len() <= MAX_VALUE).then_some(now)
}

/// After the turn: send back each ChatGPT login Codex refreshed, and when the server kept it, move the home's
/// revision with it so the next turn here does not overwrite the newer file with what it already holds.
pub async fn send_back(client: &crate::client::Client, attempt: &str, delivered: &[Delivered]) {
    for d in delivered {
        let Some(value) = refreshed(d) else { continue };
        let body = json!({"attempt": attempt, "subscription": d.id, "revision": d.revision, "value": value});
        match client.post("runner-person-subscription-refresh", &body, Some(&format!("subscription:{attempt}:{}", d.id))).await {
            Ok(answer) if answer["stored"].as_bool() == Some(true) => {
                if let (Some(home), Some(next)) = (&d.home, answer["revision"].as_i64()) {
                    let _ = write_private(&home.join(REVISION_FILE), &next.to_string());
                }
            }
            Ok(_) => {}
            Err(e) => crate::util::diagnostic(&format!("Tico runner: could not send a refreshed sign-in back: {}", e.detail)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir() -> PathBuf {
        let d = std::env::temp_dir().join(format!("tico-subs-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn a_token_replaces_every_other_sign_in_variable_of_its_runtime_only() {
        let state = dir();
        let mut env: Env = [("ANTHROPIC_API_KEY".into(), "sk-ant-computer".into()), ("ACME_KEY".into(), "acme".into())].into();
        let (mut values, mut keys) = (vec![], BTreeSet::from(["ANTHROPIC_API_KEY".to_string()]));
        let items = json!([{"id": "s1", "runtime": "claude", "kind": "claude_token", "env": "CLAUDE_CODE_OAUTH_TOKEN",
                            "value": "sk-ant-oat01-synthetic", "revision": 1, "billing": "subscription"}]);
        let got = apply(&state, &items, &mut env, &mut values, &mut keys);
        assert_eq!(got.len(), 1);
        assert_eq!(env.get("CLAUDE_CODE_OAUTH_TOKEN").map(String::as_str), Some("sk-ant-oat01-synthetic"));
        assert!(!env.contains_key("ANTHROPIC_API_KEY"));
        assert_eq!(env["ACME_KEY"], "acme");
        assert!(keys.contains("CLAUDE_CODE_OAUTH_TOKEN") && !keys.contains("ANTHROPIC_API_KEY"));
        assert_eq!(values, vec!["sk-ant-oat01-synthetic".to_string()]);
        assert_eq!(got[0].billing, "subscription");
        assert!(got[0].binding().is_none());
        // A variable that does not sign the runtime in is never set.
        let mut env2 = Env::new();
        let bad = json!([{"id": "s2", "runtime": "claude", "env": "PATH", "value": "/evil", "revision": 1}]);
        assert!(apply(&state, &bad, &mut env2, &mut vec![], &mut BTreeSet::new()).is_empty());
        assert!(!env2.contains_key("PATH"));
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn a_chatgpt_login_gets_its_own_home_kept_until_its_revision_moves() {
        let state = dir();
        let item = |rev: i64, value: &str| json!([{"id": "s3", "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                                                    "value": value, "revision": rev, "billing": "subscription"}]);
        let mut env: Env = [("CODEX_HOME".into(), "/computer/.codex".into())].into();
        let got = apply(&state, &item(1, "{\"tokens\":1}"), &mut env, &mut vec![], &mut BTreeSet::new());
        let home = got[0].home.clone().unwrap();
        assert!(home.starts_with(&state) && home.ends_with("subscriptions/s3/codex"));
        let file = home.join("auth.json");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "{\"tokens\":1}");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&file).unwrap().permissions().mode() & 0o777, 0o600);
        point_homes(&got, &mut env);
        assert_eq!(env["CODEX_HOME"], home.display().to_string());
        assert_eq!(got[0].binding().as_deref(), Some(":subscription:s3"));
        // Codex refreshed it: that is sent back, and the same revision handed again keeps the refreshed file.
        std::fs::write(&file, "{\"tokens\":2}").unwrap();
        assert_eq!(refreshed(&got[0]).as_deref(), Some("{\"tokens\":2}"));
        let again = apply(&state, &item(1, "{\"tokens\":1}"), &mut Env::new(), &mut vec![], &mut BTreeSet::new());
        assert_eq!(again[0].written, "{\"tokens\":2}");
        assert_eq!(refreshed(&again[0]), None);
        // A new revision (the person reconnected) replaces it.
        let moved = apply(&state, &item(2, "{\"tokens\":3}"), &mut Env::new(), &mut vec![], &mut BTreeSet::new());
        assert_eq!(std::fs::read_to_string(moved[0].file.as_ref().unwrap()).unwrap(), "{\"tokens\":3}");
        // An id that could leave the subscriptions folder is refused.
        let escape = json!([{"id": "../x", "runtime": "codex", "file": "auth.json", "value": "{}", "revision": 1}]);
        assert!(apply(&state, &escape, &mut Env::new(), &mut vec![], &mut BTreeSet::new()).is_empty());
        std::fs::remove_dir_all(&state).unwrap();
    }
}
