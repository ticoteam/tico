//! Once per existing bot, the legacy credentials it could read (its own `secrets/<bot>.env`, `_shared.env`,
//! declared and runtime keys) are kept as vault grants, so nothing it uses today breaks (service.py
//! `migrate_credentials`, `protect_credential_files`). The server names the bots still to migrate.

use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use regex::Regex;
use serde_json::{json, Value};

use crate::client::ApiError;
use crate::config::Config;
use crate::signin::read_env;
use crate::util::text_of;

const TEAM_KEYS_FILE: &str = "_team_model.env";
/// A sign-in key only, never a bot's credential (service.py LOGIN_ONLY): the keys of runtimes whose CLI turns a key
/// into its own login (`api_key_login`).
fn login_only() -> Vec<String> {
    crate::harness_tools::runtimes()
        .into_iter()
        .filter(|r| !crate::harness_tools::caps(r, "").api_key_login.is_empty())
        .flat_map(|r| crate::harness_tools::api_key_env(&r))
        .collect()
}

/// The keys a bot of `runtime` reads in its turns: the manifest's `api_key_env` where the runtime takes keys at all
/// and does not turn them into a login.
fn runtime_keys(runtime: &str) -> Vec<String> {
    let caps = crate::harness_tools::caps(runtime, "");
    if caps.api_keys.is_empty() || !caps.api_key_login.is_empty() {
        return vec![];
    }
    crate::harness_tools::api_key_env(runtime)
}

/// The grants one bot keeps: what it could read before grants, minus names the runner sets itself.
pub fn credentials_for(
    config: &Config,
    bot: &str,
    entry: &Value,
    process: &BTreeMap<String, String>,
) -> Vec<Value> {
    let secrets = config.projects_dir().join("secrets");
    let shared_file = read_env(&secrets.join("_shared.env"));
    let mut shared = read_env(&secrets.join(TEAM_KEYS_FILE));
    shared.extend(process.iter().map(|(k, v)| (k.clone(), v.clone())));
    shared.extend(shared_file.clone());
    let bot_config = &entry["config"];
    let declared = crate::manifest::read(&crate::readiness::local_path(config, bot))
        .filter(Value::is_object)
        .unwrap_or_else(|| bot_config.clone());
    // Insertion order as Python's dict: the bot's own file, declared keys, `_shared.env`, runtime keys.
    let mut values: Vec<(String, String)> = read_env(&secrets.join(format!("{bot}.env")))
        .into_iter()
        .collect();
    let has = |values: &Vec<(String, String)>, key: &str| values.iter().any(|(k, _)| k == key);
    let profile_re = Regex::new(r"^[a-z0-9]+(?:-[a-z0-9]+)*$").unwrap();
    for access in crate::manifest::tools_of(&declared)
        .as_array()
        .into_iter()
        .flatten()
        .filter(|a| a.is_object())
    {
        let (key, profile) = (
            crate::manifest::text_or_empty(access.get("env")),
            crate::manifest::text_or_empty(access.get("credential_profile")),
        );
        let source = if !profile.is_empty() && profile_re.is_match(&profile) {
            read_env(&secrets.join(format!("{profile}.env")))
        } else {
            shared.clone()
        };
        if let Some(v) = source
            .get(&key)
            .filter(|v| !key.is_empty() && !v.is_empty() && !has(&values, &key))
        {
            values.push((key.clone(), v.clone()));
        }
    }
    // Every existing bot could read the computer's `_shared.env` before grants, and its scripts may use a key
    // without declaring it, so each one keeps them all. Only bots created from now on start with none.
    let login_only = login_only();
    for (key, value) in &shared_file {
        if !value.is_empty() && !login_only.contains(key) && !has(&values, key) {
            values.push((key.clone(), value.clone()));
        }
    }
    let mut runtimes = vec![text_of(&bot_config["runtime"])];
    if let Some(hop) = crate::fallback::configured(bot_config) {
        runtimes.push(hop.runtime);
    }
    for runtime in runtimes {
        for key in runtime_keys(&runtime) {
            if let Some(v) = shared
                .get(&key)
                .filter(|v| !v.is_empty() && !has(&values, &key))
            {
                values.push((key.clone(), v.clone()));
            }
        }
    }
    let mut seen = std::collections::HashSet::new();
    values
        .into_iter()
        .filter(|(k, v)| {
            !k.is_empty()
                && !v.is_empty()
                && !crate::env::reserved_credential(k)
                && !["OP_SERVICE_ACCOUNT_TOKEN", "HUB_INGEST_TOKEN"].contains(&k.as_str())
                && seen.insert(k.clone())
        })
        .map(|(key, value)| {
            let path = Path::new(&value);
            let file = value.starts_with('/') && path.starts_with(&secrets) && path.is_file();
            match file.then(|| std::fs::read_to_string(path).ok()).flatten() {
                Some(text) => json!({"env": key, "value": text, "kind": "file"}),
                None => json!({"env": key, "value": value, "kind": "api_key"}),
            }
        })
        .collect()
}

/// A full pass: post each pending bot's grants, then close the legacy files to bot shells under isolation.
pub async fn migrate(
    config: &Config,
    client: &crate::client::Client,
    assignments: &[Value],
) -> Result<(), ApiError> {
    let reply = client.get("runner-credential-migration").await?;
    let pending: Vec<String> = reply["bots"]
        .as_array()
        .into_iter()
        .flatten()
        .map(text_of)
        .collect();
    let process: BTreeMap<String, String> = std::env::vars().collect();
    for entry in assignments {
        let bot = text_of(&entry["bot"]);
        if !pending.contains(&bot) {
            continue;
        }
        let credentials = credentials_for(config, &bot, entry, &process);
        client
            .post(
                "runner-credential-migration",
                &json!({"bot": bot, "credentials": credentials}),
                None,
            )
            .await?;
    }
    protect_credential_files(&config.projects_dir().join("secrets"));
    Ok(())
}

/// With isolation, the legacy files belong to the supervisor, closed to bot shells; they use vault grants.
fn protect_credential_files(secrets: &Path) {
    if crate::isolation::identity().is_none() || !secrets.is_dir() || secrets.is_symlink() {
        return;
    }
    // SAFETY: geteuid and getegid have no preconditions.
    let (uid, gid) = unsafe { (libc::geteuid(), libc::getegid()) };
    fn walk(path: &Path, uid: u32, gid: u32) {
        let Ok(meta) = std::fs::symlink_metadata(path) else {
            return;
        };
        if meta.file_type().is_symlink() {
            return;
        }
        let _ = std::os::unix::fs::lchown(path, Some(uid), Some(gid));
        let _ = std::fs::set_permissions(
            path,
            std::fs::Permissions::from_mode(if meta.is_dir() { 0o700 } else { 0o600 }),
        );
        if meta.is_dir() {
            for entry in std::fs::read_dir(path).into_iter().flatten().flatten() {
                walk(&entry.path(), uid, gid);
            }
        }
    }
    walk(secrets, uid, gid);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pending_bot_keeps_its_own_shared_declared_and_runtime_keys_but_no_reserved_one() {
        let dir =
            std::env::temp_dir().join(format!("tico-migrate-{}", uuid::Uuid::new_v4().simple()));
        let secrets = dir.join("secrets");
        std::fs::create_dir_all(&secrets).unwrap();
        std::fs::create_dir_all(dir.join("bot-assistant")).unwrap();
        std::fs::write(
            dir.join("bot-assistant").join("bot.yaml"),
            "tools:\n  - env: SEARCH_KEY\n",
        )
        .unwrap();
        std::fs::write(
            secrets.join("assistant.env"),
            "OWN=1\nHUB_TOKEN=nope\n# NOTE=x\n",
        )
        .unwrap();
        std::fs::write(secrets.join("cert.pem"), "PEM").unwrap();
        std::fs::write(
            secrets.join("_shared.env"),
            format!(
                "SHARED=2\nOPENAI_API_KEY=login\nCERT={}\n",
                secrets.join("cert.pem").display()
            ),
        )
        .unwrap();
        std::fs::write(secrets.join(TEAM_KEYS_FILE), "ANTHROPIC_API_KEY=team\n").unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"runner_id": "r1", "token": "t", "url": "http://127.0.0.1:9", "projects_dir": dir}).to_string()).unwrap();
        let config = Config::load(&path).unwrap();
        let process = BTreeMap::from([("SEARCH_KEY".to_string(), "from-env".to_string())]);
        let got = credentials_for(
            &config,
            "assistant",
            &json!({"bot": "assistant", "config": {"runtime": "claude"}}),
            &process,
        );
        let pairs: Vec<(String, String, String)> = got
            .iter()
            .map(|c| {
                (
                    text_of(&c["env"]),
                    text_of(&c["value"]),
                    text_of(&c["kind"]),
                )
            })
            .collect();
        let p = |k: &str, v: &str, kind: &str| (k.to_string(), v.to_string(), kind.to_string());
        assert_eq!(
            pairs,
            vec![
                p("OWN", "1", "api_key"),
                p("SEARCH_KEY", "from-env", "api_key"),
                p("CERT", "PEM", "file"),
                p("SHARED", "2", "api_key"),
                p("ANTHROPIC_API_KEY", "team", "api_key")
            ]
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
