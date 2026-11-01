//! The company subscription a turn signs in with: its computer's for that runtime (backend/model_subscriptions.py), read with the run's own token
//! from `credential-runtime` next to its granted credentials, so it reaches whichever computer runs the turn and never
//! the bot's repository.
//!
//! A turn may be handed two, its runtime's and its fallback's; each process gets only its own (`start`). An
//! environment subscription (`env`: a Claude setup-token as CLAUDE_CODE_OAUTH_TOKEN, an Anthropic key) is set in that
//! process's environment, and the runtime's other sign-in variables are taken out of the turn's so the CLI cannot
//! prefer one of them (Claude Code ranks ANTHROPIC_API_KEY above the token). The computer's own key for that runtime is
//! not added (`Runner::turn_key`). A home subscription (`file`: a ChatGPT login or an OpenAI key, Codex's `auth.json`)
//! gets a home of its own, `<bot state>/subscriptions/<id>/<runtime>`, kept on this computer between turns so the
//! CLI's threads resume there; that process's CODEX_HOME points at it and its conversation key names it, so no thread
//! begun on another login is resumed on it. The sign-in file itself is there only while a process of a turn handed
//! that subscription runs: written 0600 when that runtime starts (`start`), removed when the last turn holding that
//! home lets go of it (the turn moves to another runtime, `finish`, the vault's drop for a turn that ends early), and
//! swept from every home when the runner starts (`sweep`), so taking a subscription off a computer, or deleting it,
//! leaves no copy behind. When Codex refreshed the login during the turn the new file goes back
//! (`runner-model-subscription-refresh`) at the revision the turn had, so the next computer starts from it; this
//! runner keeps it in memory for its own next turn at that revision, unless the server refused it (`send_back`).
//!
//! Every value handed is masked in all the turn leaves, whichever process it went to (`values`, the turn's redaction
//! list): a token whole, and a sign-in file whole and each secret string inside it (Codex's access, refresh and id
//! tokens, its OPENAI_API_KEY), as handed, as kept here, as found in place, and as Codex refreshed it during the turn.
//!
//! One computer is one trust group (SECURITY.md): every bot on it runs as the same OS user, so another bot's turn here
//! could read a subscription's home while a turn runs on it, or a running turn's environment.

use std::collections::{BTreeSet, HashMap};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};

use serde_json::{json, Value};

use crate::env::Env;
use crate::util::text_of;

const MAX_VALUE: usize = 64 * 1024;
/// The sign-in files a home subscription may hold; nothing else in its home is removed.
const FILES: &[&str] = &["auth.json"];
/// Left by an older runner, which kept the file between turns.
const LEGACY_REVISION_FILE: &str = ".tico-revision";
/// The shortest string inside a sign-in file masked on its own (tokens and keys are far longer).
const MIN_SECRET: usize = 16;

/// One subscription handed to a turn.
#[derive(Clone, Debug, PartialEq)]
pub struct Delivered {
    pub id: String,
    pub runtime: String,
    pub kind: String,
    pub revision: i64,
    pub billing: &'static str,
    /// The variable an environment subscription is set in.
    pub env: Option<String>,
    /// The CLI's own home (CODEX_HOME) for a home subscription, and its sign-in file there.
    pub home: Option<PathBuf>,
    pub file: Option<PathBuf>,
    /// As the server handed it.
    value: String,
    /// What the file held when this turn put it in place (`start`).
    pub written: String,
    /// What Codex left in the file when the turn let go of it, when it refreshed the login (`release`).
    pub refreshed: Option<String>,
    /// The turn holds the home's sign-in file now: from its runtime's `start` to `release`.
    pub held: bool,
}

impl Delivered {
    /// The conversation key's suffix: a thread begun on one sign-in (the computer's own, another subscription) is
    /// never resumed on this one, and a home subscription's threads live in that home.
    pub fn binding(&self) -> Option<String> {
        Some(format!(":subscription:{}", self.id))
    }
}

/// The homes whose sign-in file is in place, with how many running turns hold each, and the logins Codex refreshed
/// here, by home, at the revision they were handed with (in memory only).
#[derive(Default)]
struct Homes {
    live: HashMap<PathBuf, Live>,
    kept: HashMap<PathBuf, (i64, String)>,
}

struct Live {
    turns: usize,
    revision: i64,
}

fn homes() -> MutexGuard<'static, Homes> {
    static HOMES: OnceLock<Mutex<Homes>> = OnceLock::new();
    HOMES.get_or_init(Default::default).lock().unwrap_or_else(|e| e.into_inner())
}

fn safe_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 80 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

fn billing(value: &Value) -> &'static str {
    if value.as_str() == Some("api") { "api" } else { "subscription" }
}

/// What to mask for a sign-in file: the text whole and every string in it long enough to be a token or key (not
/// timestamps), at any depth.
pub fn secrets_in(text: &str) -> Vec<String> {
    fn walk(v: &Value, out: &mut Vec<String>) {
        match v {
            Value::String(s) => {
                let stamp = s.len() <= 40 && s.as_bytes().get(..5).is_some_and(|b| b[..4].iter().all(u8::is_ascii_digit) && b[4] == b'-');
                if s.trim().len() >= MIN_SECRET && !stamp {
                    out.push(s.clone());
                }
            }
            Value::Array(items) => items.iter().for_each(|i| walk(i, out)),
            Value::Object(map) => map.values().for_each(|i| walk(i, out)),
            _ => {}
        }
    }
    let mut out = vec![text.to_string()];
    if let Ok(v) = serde_json::from_str::<Value>(text) {
        walk(&v, &mut out);
    }
    out
}

/// Read each subscription the server handed this turn; none is put in place until its runtime starts (`start`).
/// An environment one's runtime's other sign-in variables leave `env` and `keys` (the turn's vault variables) now.
/// Every value joins `values`, the turn's redaction list, for the whole turn: a turn's two processes write to the
/// same checkout and history. A malformed item is left out and said in the runner's log; the turn then runs as it
/// would have without it.
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
        let mut delivered = Delivered {
            id: id.clone(), runtime: runtime.clone(), kind: text_of(&item["kind"]),
            revision: item["revision"].as_i64().unwrap_or(0), billing: billing(&item["billing"]),
            env: None, home: None, file: None, value: value.clone(), written: value.clone(), refreshed: None, held: false,
        };
        let env_name = text_of(&item["env"]);
        if !env_name.is_empty() {
            if !crate::harness_tools::api_key_env(&runtime).contains(&env_name) {
                crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {env_name} does not sign in {runtime}"));
                continue;
            }
            for other in crate::harness_tools::api_key_env(&runtime) {
                env.remove(&other);
                keys.remove(&other);
            }
            values.push(value);
            delivered.env = Some(env_name);
        } else {
            let name = text_of(&item["file"]);
            if !FILES.contains(&name.as_str()) {
                crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {id} names no known file"));
                continue;
            }
            let home = crate::isolation::bot_state(state_dir).join("subscriptions").join(&id).join(&runtime);
            values.extend(secrets_in(&value));
            if let Some((_, kept)) = homes().kept.get(&home) {
                values.extend(secrets_in(kept));
            }
            delivered.file = Some(home.join(&name));
            delivered.home = Some(home);
        }
        out.push(delivered);
    }
    out
}

/// Sign `runtime` in, and only it, in `env`, the environment of its process about to start: the other runtimes'
/// subscriptions leave it (a hop's environment is copied from the turn's first), and their homes are let go, since
/// the host before it has stopped. A home subscription's file is put in place now. Returns what joins the turn's
/// redaction list: the file as found in place, and what Codex refreshed in a home let go. A home that cannot be made
/// leaves its subscription out of the turn, said in the runner's log.
pub fn start(delivered: &mut Vec<Delivered>, runtime: &str, env: &mut Env) -> Vec<String> {
    let mut secrets = vec![];
    for d in delivered.iter_mut().filter(|d| d.runtime != runtime) {
        secrets.extend(release(d));
        if let Some(name) = &d.env {
            if env.get(name) == Some(&d.value) {
                env.remove(name);
            }
        }
        if let (Some(home), Some(var)) = (&d.home, crate::profiles::home_var(&d.runtime)) {
            if env.get(&var).is_some_and(|v| Path::new(v) == home) {
                env.remove(&var);
            }
        }
    }
    let Some(at) = delivered.iter().position(|d| d.runtime == runtime) else { return secrets };
    let d = &mut delivered[at];
    if let Some(name) = d.env.clone() {
        for other in crate::harness_tools::api_key_env(runtime) {
            env.remove(&other);
        }
        env.insert(name, d.value.clone());
        return secrets;
    }
    let (Some(home), Some(file)) = (d.home.clone(), d.file.clone()) else { return secrets };
    if !d.held {
        let name = file.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        match place(&home, &name, &d.value, d.revision) {
            Ok(written) => {
                if written != d.value {
                    secrets.extend(secrets_in(&written));
                }
                d.written = written;
                d.held = true;
            }
            Err(e) => {
                crate::util::diagnostic(&format!("Tico runner: left a subscription out of this turn: {e}"));
                delivered.remove(at);
                return secrets;
            }
        }
    }
    if let Some(var) = crate::profiles::home_var(runtime) {
        for key in crate::harness_tools::caps(runtime, "").profile_home_overrides {
            env.remove(&key);
        }
        env.insert(var, home.display().to_string());
    }
    secrets
}

/// Hold `home/name` for one turn. The first turn on a home writes the file: the login Codex refreshed here at this
/// revision, else `value`. A turn joining one already running keeps the file in place (Codex may have refreshed it)
/// unless it brings a newer revision (someone reconnected it). Returns what the file holds now.
fn place(home: &Path, name: &str, value: &str, revision: i64) -> Result<String, String> {
    let mut homes = homes();
    crate::isolation::mkdir(home, 0o700).map_err(|e| format!("cannot make a subscription home: {e}"))?;
    let path = home.join(name);
    let live = homes.live.get(home).map(|l| l.revision);
    let current = match live {
        Some(at) if revision <= at => std::fs::read_to_string(&path).ok().filter(|t| !t.trim().is_empty()),
        _ => None,
    };
    let text = match current {
        Some(text) => text,
        None => {
            let text = match homes.kept.get(home) {
                Some((at, kept)) if live.is_none() && *at == revision => kept.clone(),
                _ => value.to_string(),
            };
            write_private(&path, &text)?;
            text
        }
    };
    let entry = homes.live.entry(home.to_path_buf()).or_insert(Live { turns: 0, revision });
    entry.turns += 1;
    entry.revision = entry.revision.max(revision);
    Ok(text)
}

fn write_private(path: &Path, value: &str) -> Result<(), String> {
    let staged = path.with_extension(format!("tico-{}", uuid::Uuid::new_v4().simple()));
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&staged)
        .map_err(|e| format!("cannot write a subscription file: {e}"))?;
    let written = crate::isolation::chown_file(&f).map_err(|e| e.to_string())
        .and_then(|_| f.write_all(value.as_bytes()).map_err(|e| e.to_string()));
    drop(f);
    written.and_then(|_| std::fs::rename(&staged, path).map_err(|e| e.to_string()))
        .inspect_err(|_| { let _ = std::fs::remove_file(&staged); })
}

/// Remove the sign-in files from one subscription home (and what a write cut short left), keeping its sessions.
fn clear(home: &Path) {
    let Ok(entries) = std::fs::read_dir(home) else { return };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let staged = FILES.iter().any(|f| name.strip_prefix(f.trim_end_matches(".json")).is_some_and(|rest| rest.starts_with(".tico-")));
        if FILES.contains(&name.as_str()) || staged || name == LEGACY_REVISION_FILE {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// At the runner's start, before any turn: no turn holds a home, so a sign-in file left in one (the runner stopped
/// mid-turn, or an older runner kept it) is removed.
pub fn sweep(state_dir: &Path) {
    let root = crate::isolation::bot_state(state_dir).join("subscriptions");
    for sub in std::fs::read_dir(&root).into_iter().flatten().flatten() {
        for home in std::fs::read_dir(sub.path()).into_iter().flatten().flatten() {
            if home.file_type().is_ok_and(|t| t.is_dir()) {
                clear(&home.path());
            }
        }
    }
}

/// After the turn's harnesses stopped: let go of every home the turn still holds (`release`). Returns every secret
/// in the refreshed files, for the turn's redaction list. Later calls do nothing.
pub fn finish(delivered: &mut [Delivered]) -> Vec<String> {
    delivered.iter_mut().flat_map(release).collect()
}

/// Once the process that ran on this home stopped: note the login Codex refreshed (`Delivered::refreshed`, sent back
/// by `send_back`), let go of the home, and remove its sign-in file when no other running turn holds it. Returns every
/// secret in the refreshed file. Nothing for a home not held.
fn release(d: &mut Delivered) -> Vec<String> {
    let mut secrets = vec![];
    let Some(home) = d.home.clone().filter(|_| d.held) else { return secrets };
    d.held = false;
    let mut homes = homes();
    if let Some(now) = changed(d) {
        secrets.extend(secrets_in(&now));
        if homes.live.get(&home).is_some_and(|l| l.revision == d.revision) {
            homes.kept.insert(home.clone(), (d.revision, now.clone()));
        }
        d.refreshed = Some(now);
    }
    match homes.live.get_mut(&home) {
        Some(live) if live.turns > 1 => live.turns -= 1,
        _ => {
            homes.live.remove(&home);
            clear(&home);
        }
    }
    secrets
}

pub fn for_runtime<'a>(delivered: &'a [Delivered], runtime: &str) -> Option<&'a Delivered> {
    delivered.iter().find(|d| d.runtime == runtime)
}

/// The file as Codex left it, when it refreshed a ChatGPT login since the turn began; None when it is as written.
fn changed(d: &Delivered) -> Option<String> {
    if d.kind != "codex_login" {
        return None;
    }
    let now = std::fs::read_to_string(d.file.as_ref()?).ok()?;
    (now != d.written && !now.trim().is_empty() && now.len() <= MAX_VALUE).then_some(now)
}

/// After the turn (`finish`): send back each ChatGPT login Codex refreshed. The copy this runner kept follows the
/// server's answer: kept there, it moves to the new revision, so the next turn here starts from it rather than the
/// older value the server held; refused (another account, nothing new, or the subscription moved on), it is dropped,
/// so the next turn here starts from what the server holds. Unanswered (the network), it stays: it is the newest login
/// this computer has.
pub async fn send_back(client: &crate::client::Client, attempt: &str, delivered: &[Delivered]) {
    for d in delivered {
        let Some(value) = &d.refreshed else { continue };
        let body = json!({"attempt": attempt, "subscription": d.id, "revision": d.revision, "value": value});
        match client.post("runner-model-subscription-refresh", &body, Some(&format!("subscription:{attempt}:{}", d.id))).await {
            Ok(answer) => {
                let Some(home) = &d.home else { continue };
                let mut homes = homes();
                if homes.kept.get(home).is_none_or(|(_, kept)| kept != value) {
                    continue;   // another turn here kept a later one
                }
                match (answer["stored"].as_bool() == Some(true), answer["revision"].as_i64()) {
                    (true, Some(next)) => {
                        if let Some(kept) = homes.kept.get_mut(home) {
                            kept.0 = next;
                        }
                    }
                    (true, None) => {}
                    (false, _) => {
                        homes.kept.remove(home);
                    }
                }
            }
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

    /// A turn handed `items` whose `runtime` process starts.
    fn turn(state: &Path, items: &Value, runtime: &str) -> Vec<Delivered> {
        let mut got = apply(state, items, &mut Env::new(), &mut vec![], &mut BTreeSet::new());
        start(&mut got, runtime, &mut Env::new());
        got
    }

    #[test]
    fn a_token_replaces_every_other_sign_in_variable_of_its_runtime_only() {
        let state = dir();
        let mut env: Env = [("ANTHROPIC_API_KEY".into(), "sk-ant-computer".into()), ("ACME_KEY".into(), "acme".into())].into();
        let (mut values, mut keys) = (vec![], BTreeSet::from(["ANTHROPIC_API_KEY".to_string()]));
        let items = json!([{"id": "s1", "runtime": "claude", "kind": "claude_token", "env": "CLAUDE_CODE_OAUTH_TOKEN",
                            "value": "sk-ant-oat01-synthetic", "revision": 1, "billing": "subscription"}]);
        let mut got = apply(&state, &items, &mut env, &mut values, &mut keys);
        assert_eq!(got.len(), 1);
        assert!(!env.contains_key("ANTHROPIC_API_KEY") && !env.contains_key("CLAUDE_CODE_OAUTH_TOKEN"), "{env:?}");
        assert!(!keys.contains("ANTHROPIC_API_KEY"));
        assert_eq!(values, vec!["sk-ant-oat01-synthetic".to_string()]);
        // Set when Claude starts.
        assert!(start(&mut got, "claude", &mut env).is_empty());
        assert_eq!(env.get("CLAUDE_CODE_OAUTH_TOKEN").map(String::as_str), Some("sk-ant-oat01-synthetic"));
        assert_eq!(env["ACME_KEY"], "acme");
        assert_eq!(got[0].billing, "subscription");
        assert_eq!(got[0].binding().as_deref(), Some(":subscription:s1"));
        // A variable that does not sign the runtime in is never set.
        let mut env2 = Env::new();
        let bad = json!([{"id": "s2", "runtime": "claude", "env": "PATH", "value": "/evil", "revision": 1}]);
        assert!(apply(&state, &bad, &mut env2, &mut vec![], &mut BTreeSet::new()).is_empty());
        assert!(!env2.contains_key("PATH"));
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn a_chatgpt_login_gets_its_own_home_and_its_file_lasts_only_while_a_turn_holds_it() {
        let state = dir();
        let item = |rev: i64, value: &str| json!([{"id": "s3", "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                                                    "value": value, "revision": rev, "billing": "subscription"}]);
        let mut env: Env = [("CODEX_HOME".into(), "/computer/.codex".into())].into();
        let mut got = apply(&state, &item(1, "{\"tokens\":1}"), &mut env, &mut vec![], &mut BTreeSet::new());
        let home = got[0].home.clone().unwrap();
        assert!(home.starts_with(&state) && home.ends_with("subscriptions/s3/codex"));
        let file = home.join("auth.json");
        assert!(!file.exists(), "written before Codex starts");
        start(&mut got, "codex", &mut env);
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "{\"tokens\":1}");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&file).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(env["CODEX_HOME"], home.display().to_string());
        assert_eq!(got[0].binding().as_deref(), Some(":subscription:s3"));
        // Codex refreshed it and writes a session; a second turn on the same home joins with the refreshed file.
        std::fs::write(&file, "{\"tokens\":2}").unwrap();
        std::fs::create_dir_all(home.join("sessions")).unwrap();
        std::fs::write(home.join("sessions/t1.jsonl"), "{}").unwrap();
        let mut again = turn(&state, &item(1, "{\"tokens\":1}"), "codex");
        assert_eq!(again[0].written, "{\"tokens\":2}");
        // The first turn ends: the refresh is noted for sending back, the file stays for the turn still running.
        assert_eq!(finish(&mut got), vec!["{\"tokens\":2}".to_string()]);
        assert_eq!(got[0].refreshed.as_deref(), Some("{\"tokens\":2}"));
        assert!(file.exists());
        assert!(finish(&mut got).is_empty(), "a second finish changes nothing");
        // The last turn ends: the file goes, the sessions stay.
        assert!(finish(&mut again).is_empty());
        assert_eq!(again[0].refreshed, None);
        assert!(!file.exists());
        assert!(home.join("sessions/t1.jsonl").exists());
        // The next turn at that revision starts from the refreshed login this runner kept, not the older value.
        let mut next = turn(&state, &item(1, "{\"tokens\":1}"), "codex");
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "{\"tokens\":2}");
        // A new revision (someone reconnected it) replaces it, even under a turn still running.
        let mut moved = turn(&state, &item(2, "{\"tokens\":3}"), "codex");
        assert_eq!(std::fs::read_to_string(moved[0].file.as_ref().unwrap()).unwrap(), "{\"tokens\":3}");
        finish(&mut next);
        finish(&mut moved);
        assert!(!file.exists());
        // A turn that ends early lets go through its vault.
        let mut vault = crate::env::Vault::default();
        vault.subscriptions = turn(&state, &item(2, "{\"tokens\":3}"), "codex");
        assert!(file.exists());
        drop(vault);
        assert!(!file.exists());
        // An id that could leave the subscriptions folder is refused.
        let escape = json!([{"id": "../x", "runtime": "codex", "file": "auth.json", "value": "{}", "revision": 1}]);
        assert!(apply(&state, &escape, &mut Env::new(), &mut vec![], &mut BTreeSet::new()).is_empty());
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn each_process_of_a_turn_gets_only_its_own_runtimes_subscription() {
        let state = dir();
        let token = "sk-ant-oat01-synthetic-primary";
        let login = r#"{"tokens":{"refresh_token":"rt_synthetic_fallback_cccccccc","account_id":"acct"}}"#;
        let items = json!([
            {"id": "s9", "runtime": "claude", "kind": "claude_token", "env": "CLAUDE_CODE_OAUTH_TOKEN", "value": token, "revision": 1},
            {"id": "s10", "runtime": "codex", "kind": "codex_login", "file": "auth.json", "value": login, "revision": 1},
        ]);
        // Claude runs first, Codex is its fallback.
        let mut env: Env = [("CODEX_HOME".into(), "/computer/.codex".into())].into();
        let mut values = vec![];
        let mut got = apply(&state, &items, &mut env, &mut values, &mut BTreeSet::new());
        let home = got[1].home.clone().unwrap();
        let file = home.join("auth.json");
        // Both are masked for the whole turn, whichever process they go to.
        let r = crate::redact::Redactor::new(&values);
        for leaked in [token, "rt_synthetic_fallback_cccccccc"] {
            assert!(!r.scrub_text(leaked).contains(leaked), "{leaked}");
        }
        start(&mut got, "claude", &mut env);
        assert_eq!(env["CLAUDE_CODE_OAUTH_TOKEN"], token);
        assert_eq!(env["CODEX_HOME"], "/computer/.codex", "Claude's process was pointed at the fallback's home");
        assert!(!file.exists(), "the fallback's login was in place while only Claude ran");
        // No local login and no named fallback: the handed subscription wins before Gateway.
        let config = json!({"runtime": "claude", "harness": "claude", "model": "claude-sonnet-5-5"});
        let choices = vec![json!({"runtime": "codex", "model": "gpt-6.1-sol"}),
                           json!({"runtime": "pi", "model": "tico-mid-1.0"})];
        let ready = |rt: &str| for_runtime(&got, rt).is_some();
        let chosen = crate::fallback::default_choice(&config, &choices, &env, ready).unwrap();
        assert_eq!(chosen.runtime, "codex");
        assert!(chosen.default);
        assert!(crate::fallback::default_choice(&config, &choices, &env, |_| false).is_none());
        // The hop: its environment is copied from the turn's; it gets the Codex home and loses Claude's token.
        let mut hop = env.clone();
        start(&mut got, "codex", &mut hop);
        assert_eq!(hop["CODEX_HOME"], home.display().to_string());
        assert_eq!(std::fs::read_to_string(&file).unwrap(), login);
        assert!(!hop.contains_key("CLAUDE_CODE_OAUTH_TOKEN"), "{hop:?}");
        finish(&mut got);
        assert!(!file.exists());

        // The other way round: Codex first, Claude its fallback. The Codex home is let go once Claude starts.
        let mut env = Env::new();
        let mut got = apply(&state, &items, &mut env, &mut vec![], &mut BTreeSet::new());
        start(&mut got, "codex", &mut env);
        assert!(file.exists() && !env.contains_key("CLAUDE_CODE_OAUTH_TOKEN"));
        std::fs::write(&file, r#"{"tokens":{"refresh_token":"rt_synthetic_refreshed_dddddddd","account_id":"acct"}}"#).unwrap();
        let mut hop = env.clone();
        let fresh = start(&mut got, "claude", &mut hop);
        assert!(fresh.iter().any(|s| s == "rt_synthetic_refreshed_dddddddd"), "the refresh was not masked: {fresh:?}");
        assert!(got[1].refreshed.is_some() && !got[1].held);
        assert!(!file.exists(), "the Codex login stayed after its host stopped");
        assert!(!hop.contains_key("CODEX_HOME"), "{hop:?}");
        assert_eq!(hop["CLAUDE_CODE_OAUTH_TOKEN"], token);
        assert!(finish(&mut got).is_empty(), "a home let go is not let go twice");
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[tokio::test]
    async fn a_refresh_the_server_refused_is_never_the_next_turns_login_and_an_unsent_one_is() {
        let state = dir();
        let item = |id: &str, rev: i64| json!([{"id": id, "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                                                "value": "{\"tokens\":1}", "revision": rev}]);
        let refreshed = |id: &str| {
            let mut got = turn(&state, &item(id, 1), "codex");
            std::fs::write(got[0].file.as_ref().unwrap(), "{\"tokens\":2}").unwrap();
            finish(&mut got);
            got
        };
        let kept = |id: &str| homes().kept.get(&crate::isolation::bot_state(&state).join("subscriptions").join(id).join("codex")).cloned();
        // Refused (another account, or nothing new): dropped, so the next turn starts from what the server holds.
        let got = refreshed("s11");
        let (url, seen) = crate::client::tests::serve(vec![(200, r#"{"stored": false}"#)]).await;
        send_back(&crate::client::Client::new(&url, "t", 5, 0).unwrap(), "a1", &got).await;
        assert!(seen.lock().unwrap()[0].contains("runner-model-subscription-refresh"));
        assert_eq!(kept("s11"), None);
        let mut next = turn(&state, &item("s11", 1), "codex");
        assert_eq!(next[0].written, "{\"tokens\":1}");
        finish(&mut next);
        // Kept: the copy moves to the server's new revision.
        let got = refreshed("s12");
        let (url, _) = crate::client::tests::serve(vec![(200, r#"{"stored": true, "revision": 2}"#)]).await;
        send_back(&crate::client::Client::new(&url, "t", 5, 0).unwrap(), "a2", &got).await;
        assert_eq!(kept("s12"), Some((2, "{\"tokens\":2}".to_string())));
        // Unanswered: it stays, the newest login this computer has.
        let got = refreshed("s13");
        let closed = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", closed.local_addr().unwrap());
        drop(closed);
        send_back(&crate::client::Client::new(&url, "t", 5, 0).unwrap(), "a3", &got).await;
        assert_eq!(kept("s13"), Some((1, "{\"tokens\":2}".to_string())));
        let mut next = turn(&state, &item("s13", 1), "codex");
        assert_eq!(next[0].written, "{\"tokens\":2}");
        finish(&mut next);
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn the_runner_start_removes_sign_in_files_left_in_every_home_and_keeps_sessions() {
        let state = dir();
        let root = crate::isolation::bot_state(&state).join("subscriptions");
        for home in [root.join("s4/codex"), root.join("s5/codex")] {
            std::fs::create_dir_all(home.join("sessions")).unwrap();
            for name in ["auth.json", "auth.tico-0123abcd", ".tico-revision", "sessions/t.jsonl", "config.toml"] {
                std::fs::write(home.join(name), "x").unwrap();
            }
        }
        sweep(&state);
        for home in [root.join("s4/codex"), root.join("s5/codex")] {
            for name in ["auth.json", "auth.tico-0123abcd", ".tico-revision"] {
                assert!(!home.join(name).exists(), "{name}");
            }
            assert!(home.join("sessions/t.jsonl").exists() && home.join("config.toml").exists());
        }
        sweep(&dir()); // no subscriptions folder: nothing to do
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn each_token_in_a_chatgpt_login_is_masked_on_its_own_as_handed_kept_and_refreshed() {
        let state = dir();
        let login = |n: u8| json!({
            "OPENAI_API_KEY": format!("sk-proj-synthetic-key-{n}-aaaaaaaaaaaa"),
            "tokens": {"id_token": format!("eyJsynthetic.id-token-{n}.sig"), "access_token": format!("eyJsynthetic.access-{n}.sig"),
                       "refresh_token": format!("rt_synthetic_refresh_{n}_bbbbbbbb"), "account_id": "acct"},
            "last_refresh": "2026-10-30T12:00:00.000000Z",
        }).to_string();
        let item = json!([{"id": "s6", "runtime": "codex", "kind": "codex_login", "file": "auth.json", "value": login(1), "revision": 1}]);
        let mut values = vec![];
        let mut got = apply(&state, &item, &mut Env::new(), &mut values, &mut BTreeSet::new());
        values.extend(start(&mut got, "codex", &mut Env::new()));
        let r = crate::redact::Redactor::new(&values);
        for leaked in ["sk-proj-synthetic-key-1-aaaaaaaaaaaa", "eyJsynthetic.id-token-1.sig", "eyJsynthetic.access-1.sig", "rt_synthetic_refresh_1_bbbbbbbb"] {
            let out = r.scrub_text(&format!("echo {leaked} > notes.txt"));
            assert!(!out.contains(leaked), "{out}");
        }
        // Not a secret: the refresh time stays readable.
        assert!(r.scrub_text("at 2026-10-30T12:00:00.000000Z").contains("2026-10-30T12"));
        // Codex refreshed it: the new tokens come back from `finish` for the turn's redactor, before anything is sent.
        std::fs::write(got[0].file.as_ref().unwrap(), login(2)).unwrap();
        let fresh = finish(&mut got);
        r.add(&fresh);
        for leaked in ["eyJsynthetic.access-2.sig", "rt_synthetic_refresh_2_bbbbbbbb", "eyJsynthetic.id-token-2.sig"] {
            assert!(!r.scrub_text(leaked).contains(leaked), "{leaked}");
        }
        // A file kept from that refresh is masked at the next turn too, from its start, as well as what the server handed.
        let mut values = vec![];
        let mut next = apply(&state, &item, &mut Env::new(), &mut values, &mut BTreeSet::new());
        let r = crate::redact::Redactor::new(&values);
        for leaked in ["rt_synthetic_refresh_1_bbbbbbbb", "rt_synthetic_refresh_2_bbbbbbbb"] {
            assert!(!r.scrub_text(leaked).contains(leaked), "{leaked}");
        }
        start(&mut next, "codex", &mut Env::new());
        assert_eq!(next[0].written, login(2));
        finish(&mut next);
        std::fs::remove_dir_all(&state).unwrap();
    }
}
