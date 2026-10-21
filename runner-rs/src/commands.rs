//! `enroll`, `adopt` and `doctor` (runner/__main__.py): register this computer, take bots onto it, and check
//! readiness without running a turn. Exit codes as Python's: 2 for a usage refusal, 1 for a failed request or a
//! bot that is not ready.

use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::client::Client;
use crate::config::Config;
use crate::util::text_of;

/// A refusal: (exit code, message). 2 goes out with the usage line, as argparse's `parser.error`.
pub type Refusal = (i32, String);

fn usage(message: &str) -> Refusal {
    (2, message.to_string())
}

fn failed(message: String) -> Refusal {
    (1, message)
}

/// `enroll --url --code-file --label --projects [--environment]`: POST runners/enroll with the one-time code,
/// then write the registration 0600, never over an existing one (O_EXCL), and carry the protected mail key from
/// this operator's previous registration. Returns what Python prints: runner_id, config, operator.
pub async fn enroll(config_path: &Path, url: &str, code_file: &Path, label: &str, projects: &Path, environment: &str) -> Result<Value, Refusal> {
    if config_path.exists() {
        return Err(usage("This machine is already configured; use a distinct --config to enroll another runner"));
    }
    let code = std::fs::read_to_string(code_file)
        .map_err(|e| failed(format!("cannot read {}: {e}", code_file.display())))
        .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| failed(format!("{} is not JSON: {e}", code_file.display()))))?
        .get("code")
        .cloned()
        .ok_or_else(|| failed(format!("{} has no code", code_file.display())))?;
    let client = Client::new(url, "", 15, 3).map_err(failed)?;
    let body = json!({"code": code, "label": label, "platform": crate::runner::platform()});
    let result = client.post("runners/enroll", &body, None).await.map_err(|e| failed(crate::runner::describe(&e)))?;
    let Value::Object(mut data) = result else { return Err(failed("Tico returned an invalid enrollment".into())) };
    data.insert("url".into(), json!(url));
    let projects = projects.canonicalize().or_else(|_| std::path::absolute(projects)).unwrap_or_else(|_| projects.to_path_buf());
    data.insert("projects_dir".into(), json!(projects.to_string_lossy()));
    if !environment.is_empty() {
        data.insert("environment".into(), json!(environment));
    }
    if let Some(dir) = config_path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(dir).map_err(|e| failed(format!("cannot create {}: {e}", dir.display())))?;
    }
    let text = serde_json::to_string_pretty(&Value::Object(data.clone())).unwrap_or_default();
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(config_path)
        .map_err(|e| failed(format!("cannot write {}: {e}", config_path.display())))?;
    file.write_all(text.as_bytes()).map_err(|e| failed(format!("cannot write {}: {e}", config_path.display())))?;
    drop(file);
    let config = Config { path: config_path.to_path_buf(), data };
    crate::mail_key::carry_protected(&config);
    Ok(json!({"runner_id": config.data.get("runner_id").cloned().unwrap_or(Value::Null), "config": config_path.to_string_lossy(), "operator": config.data.get("operator").cloned().unwrap_or(Value::Null)}))
}

/// `adopt --team T | --bot B...`: move the matching eligible bots to this computer, each at the generation the
/// server listed. Returns the lines to print.
pub async fn adopt(config: &Config, client: &Client, team: Option<&str>, bots: &[String]) -> Result<Vec<String>, Refusal> {
    let team = team.filter(|t| !t.is_empty());
    if team.is_none() && bots.is_empty() {
        return Err(usage("Choose --team or --bot so the assignment scope is explicit"));
    }
    let eligible = client.get("runners/eligible").await.map_err(|e| failed(crate::runner::describe(&e)))?;
    let selected: Vec<Value> = eligible
        .as_array()
        .into_iter()
        .flatten()
        .filter(|r| team.is_some_and(|t| text_of(&r["team"]) == t) || bots.contains(&text_of(&r["bot"])))
        .cloned()
        .collect();
    if selected.is_empty() {
        return Err(usage("No eligible bots match that scope"));
    }
    let mut lines = vec![];
    for row in selected {
        if text_of(&row["runner_id"]) == config.runner_id() {
            continue;
        }
        let body = json!({"bot": row["bot"], "expected_generation": row["generation"]});
        client.post("runners/adopt", &body, None).await.map_err(|e| failed(crate::runner::describe(&e)))?;
        lines.push(format!("Assigned {} to this machine", text_of(&row["bot"])));
    }
    Ok(lines)
}

/// `doctor`: the readiness the next heartbeat would report, with every bot's full check row and the base clones'
/// saved status, and no model call, clone or catalog setup. The bool is whether every row is ready (exit 0).
pub async fn doctor(config: &Config, client: &Client) -> Result<(Value, bool), Refusal> {
    let assignments = client.get("runners/assignments").await.map_err(|e| failed(crate::runner::describe(&e)))?;
    let assignments = assignments.as_array().cloned().unwrap_or_default();
    if let Some(tools) = crate::harness_tools::shared() {
        let runtimes = crate::relays::harness_runtimes(&assignments);
        let company = client.get("config").await.unwrap_or(Value::Null);
        let default = crate::relays::harness_defaults(&runtimes, &company, crate::isolation::enabled());
        let runtimes: Vec<String> = runtimes.into_iter().collect();
        tools.lock().unwrap().want(&runtimes, &default);
    }
    let candidates = crate::runner::readiness_candidates(&assignments);
    let sign = crate::signin::SignIn::new(&config.projects_dir());
    let runtimes = crate::readiness::runtime_report(config, &sign, client, &candidates).await;
    let mut checks = crate::readiness::preflight(config, &candidates, &runtimes).await;
    crate::perbot::PerBot::default().decorate(config, &mut checks, &candidates);
    let ready = checks.iter().all(|row| row["ready"] == true);
    let mut readiness = crate::readiness::document(&runtimes, &checks, &config.state_dir());
    if crate::mail_key::status(config) == Some("exposed") {
        readiness["mail_key"] = json!("exposed");
    }
    if let Some(h) = crate::harness_tools::report(&runtimes).await {
        readiness["harnesses"] = h;
    }
    readiness["engine"] = json!({"name": "rust", "build": crate::BUILD_ID});
    let (screen_on, _) = crate::screen::lock(&config.path);
    if let Some(screen) = crate::screen::report(screen_on, std::env::consts::OS, &crate::screen::missing_tools(), None, true) {
        readiness["screen"] = screen;
    }
    let repositories = crate::repositories::Repositories::new(&config.projects_dir(), &config.state_dir(), client.clone()).inspect().await;
    let mut out = Map::new();
    out.insert("runner_id".into(), json!(config.runner_id()));
    out.insert("checks".into(), json!(checks));
    out.insert("readiness".into(), readiness);
    out.insert("repositories".into(), json!(repositories));
    out.insert("credentials_directory".into(), json!(config.projects_dir().join("secrets").to_string_lossy()));
    out.insert("note".into(), json!("Checks repositories, runtime installation, and supported non-model sign-in status. No model calls were made."));
    Ok((Value::Object(out), ready))
}

/// The default registration file, as Python's `--config` default.
pub fn default_config() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config/tico/runner.json")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// A hub answering each `/api/v2/<path>` from `routes` (404 otherwise), recording each request line and body.
    async fn hub(routes: Vec<(&'static str, Value)>) -> (String, Arc<Mutex<Vec<String>>>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let log = seen.clone();
        tokio::spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                let mut got = vec![];
                let mut buf = vec![0u8; 65536];
                loop {
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    got.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&got).to_string();
                    let done = text.find("\r\n\r\n").is_some_and(|h| {
                        let length = text[..h].lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
                        got.len() >= h + 4 + length
                    });
                    if n == 0 || done {
                        break;
                    }
                }
                let text = String::from_utf8_lossy(&got).to_string();
                let path = text.split_whitespace().nth(1).unwrap_or("").trim_start_matches("/api/v2/").split('?').next().unwrap_or("").to_string();
                log.lock().unwrap().push(format!("{} {}", text.split_whitespace().next().unwrap_or(""), path) + " " + text.split("\r\n\r\n").nth(1).unwrap_or(""));
                let (status, body) = routes.iter().find(|(p, _)| *p == path).map(|(_, b)| (200, b.to_string())).unwrap_or((404, "{}".into()));
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
            }
        });
        (url, seen)
    }

    fn scratch() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-commands-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn enroll_writes_a_private_registration_once_and_refuses_an_existing_one() {
        let dir = scratch();
        let (url, seen) = hub(vec![("runners/enroll", json!({"runner_id": "r9", "token": "t", "operator": "u1"}))]).await;
        std::fs::write(dir.join("code.json"), r#"{"code": "abc"}"#).unwrap();
        let config = dir.join("conf/runner.json");
        let out = enroll(&config, &url, &dir.join("code.json"), "Mac", &dir, "env1").await.unwrap();
        assert_eq!(out, json!({"runner_id": "r9", "config": config.to_string_lossy(), "operator": "u1"}));
        assert_eq!(std::fs::metadata(&config).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(dir.join("conf")).unwrap().permissions().mode() & 0o777, 0o700);
        let saved: Value = serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(saved["url"], json!(url));
        assert_eq!(saved["environment"], "env1");
        assert_eq!(saved["projects_dir"], json!(dir.canonicalize().unwrap().to_string_lossy()));
        assert!(seen.lock().unwrap()[0].contains(r#""code":"abc""#));
        let again = enroll(&config, &url, &dir.join("code.json"), "Mac", &dir, "").await.unwrap_err();
        assert_eq!(again.0, 2);
        assert_eq!(seen.lock().unwrap().len(), 1, "no second enrollment request");
        std::fs::remove_dir_all(dir).ok();
    }

    #[tokio::test]
    async fn adopt_needs_a_scope_and_skips_bots_already_here() {
        let dir = scratch();
        let eligible = json!([
            {"bot": "a", "team": "eng", "runner_id": "other", "generation": 3},
            {"bot": "b", "team": "eng", "runner_id": "r1", "generation": 1},
            {"bot": "c", "team": "ops", "runner_id": "other", "generation": 2},
        ]);
        let (url, seen) = hub(vec![("runners/eligible", eligible), ("runners/adopt", json!({}))]).await;
        let config = Config { path: dir.join("runner.json"), data: json!({"runner_id": "r1"}).as_object().unwrap().clone() };
        let client = Client::new(&url, "t", 5, 0).unwrap();
        assert_eq!(adopt(&config, &client, None, &[]).await.unwrap_err().0, 2);
        assert_eq!(adopt(&config, &client, Some("eng"), &[]).await.unwrap(), vec!["Assigned a to this machine"]);
        assert_eq!(adopt(&config, &client, None, &["zz".into()]).await.unwrap_err().0, 2);
        let posts: Vec<String> = seen.lock().unwrap().iter().filter(|l| l.starts_with("POST")).cloned().collect();
        assert_eq!(posts, vec![r#"POST runners/adopt {"bot":"a","expected_generation":3}"#]);
        std::fs::remove_dir_all(dir).ok();
    }

    /// The keys scripts/tico `summarize` reads, and not ready (exit 1) for a bot with no checkout, which is also
    /// how an unsupported harness shows: a problem on that bot's row, not a refusal to start.
    #[tokio::test]
    async fn doctor_reports_the_summarized_keys_and_a_bot_that_is_not_ready() {
        let dir = scratch();
        let assignments = json!([{"bot": "x", "runner_id": "r1", "state": "active", "config": {"runtime": "codex", "harness": "hermes"}}]);
        let repos = json!({"repositories": [{"full_name": "Org/Repo"}, {"full_name": "../bad"}]});
        let (url, _) = hub(vec![("runners/assignments", assignments), ("runners/me/repositories", repos)]).await;
        let data = json!({"runner_id": "r1", "projects_dir": dir.to_string_lossy()});
        let config = Config { path: dir.join("runner.json"), data: data.as_object().unwrap().clone() };
        let client = Client::new(&url, "t", 5, 0).unwrap();
        let (out, ready) = doctor(&config, &client).await.unwrap();
        assert!(!ready);
        let keys: Vec<&String> = out.as_object().unwrap().keys().collect();
        assert_eq!(keys, ["runner_id", "checks", "readiness", "repositories", "credentials_directory", "note"]);
        let row = &out["checks"][0];
        assert_eq!((row["bot"].as_str(), row["ready"].as_bool()), (Some("x"), Some(false)));
        assert!(row["problems"].as_array().unwrap().iter().any(|p| p.as_str().unwrap().contains("does not run the hermes harness")));
        assert!(out["readiness"]["runtimes"].is_object() && out["readiness"]["bots"]["x"].is_object());
        assert_eq!(out["repositories"], json!([{"full_name": "Org/Repo", "state": "cloning", "last_fetch": null, "size_mb": 0}]));
        assert_eq!(out["credentials_directory"], json!(dir.join("secrets").to_string_lossy()));
        std::fs::remove_dir_all(dir).ok();
    }
}
