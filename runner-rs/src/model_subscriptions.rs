//! The company subscription a turn signs in with: its computer's for that runtime (backend/model_subscriptions.py), read with the run's own token
//! from `credential-runtime` next to its granted credentials, so it reaches whichever computer runs the turn and never
//! the bot's repository.
//!
//! A turn may use its primary, named fallback and automatic candidates. Automatic values are fetched only at the
//! hop boundary (`activate`); each process gets only its own (`start`), except a plan login kept in a home (a ChatGPT
//! login), which every process of the turn gets for its own commands, so a bot on Claude can run `codex`. An
//! environment subscription (`env`: a Claude setup-token as CLAUDE_CODE_OAUTH_TOKEN, an Anthropic key) is set in that
//! process's environment, and the runtime's other sign-in variables are taken out of the turn's so the CLI cannot
//! prefer one of them (Claude Code ranks ANTHROPIC_API_KEY above the token). The computer's own key for that runtime is
//! not added (`Runner::turn_key`). A home subscription (`file`: a ChatGPT login or an OpenAI key, Codex's `auth.json`)
//! gets a home of its own, `<bot state>/subscriptions/<id>/<runtime>`, kept on this computer between turns so the
//! CLI's threads resume there; that process's CODEX_HOME points at it and its conversation key names it, so no thread
//! begun on another login is resumed on it. A Claude plan's threads stay in the turn's own home, so every Claude plan
//! shares one conversation key (`Delivered::binding`) and a move between plans resumes the thread. The sign-in file itself is there only while a process of a turn handed
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
//! A full Claude login (`claude_login`, backend/claude_login.py) is a home subscription too, with one difference: its
//! file is Claude Code's `.credentials.json`, holding only a short-lived access token (never the refresh token), and its
//! home is the process's CLAUDE_CONFIG_DIR rather than its HOME. Every other entry of the turn's own `~/.claude`
//! (settings, skills, `projects` with the transcripts) is linked into that home, so the turn sees what it would see
//! signed in as the computer, and its sessions stay where they always were. When the plan's token is refreshed (on
//! this computer or another, claude_login.rs) the file in every home running on it is rewritten (`rewrite`) before the
//! old token is revoked or, at worst, before the turn's next call.
//!
//! An Antigravity plan (`antigravity_login`) is a home subscription made for each turn too (`TURN_HOMES`): `agy` reads its
//! sign-in from `<GEMINI_HOME>/antigravity-cli/antigravity-oauth-token` only when it believes it runs over SSH (else
//! from the system keyring), so a plan turn's GEMINI_HOME is a home of its own holding that file, and its `agy` alone
//! is told it runs over SSH, without any other Google sign-in (hosts/antigravity.rs, from `plan_home`, so even an `agy`
//! started again later never falls back to the keyring). The bot's own `~/.gemini` rules, skills and conversations are
//! linked in (`TurnHome::links`), never its settings or the folder of the computer's own sign-in, so a move between
//! plans resumes the same conversation. Its conversation summaries (`conversation_summaries.db`) are not: a SQLite
//! file cannot be shared through a link, so they do not carry across plans.
//!
//! One computer is one trust group (SECURITY.md): every bot on it runs as the same OS user, so another bot's turn here
//! could read a subscription's home while a turn runs on it, or a running turn's environment.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::ffi::{CStr, CString, OsStr};
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};

use serde_json::{json, Value};

use crate::env::Env;
use crate::util::text_of;

const MAX_VALUE: usize = 64 * 1024;
/// The sign-in files a home subscription may hold; nothing else in its home is removed.
const FILES: &[&str] = &["auth.json", CREDENTIALS];
/// Where `agy` keeps a sign-in in its home when it believes it runs over SSH.
pub const AGY_TOKEN: &str = "antigravity-cli/antigravity-oauth-token";
/// What `agy` is told so it reads and writes AGY_TOKEN, never the system keyring.
pub const OVER_SSH: (&str, &str) = ("SSH_CONNECTION", "127.0.0.1 0 127.0.0.1 0");
/// This runner can sign in, install and run Antigravity plans; the hub gives such work to no other runner.
pub const AGY_PLAN_CAPABILITY: &str = "antigravity_plan_v1";

/// A runtime whose plan login lives in a home made for each turn, which its CLI finds through `var`.
pub struct TurnHome {
    pub runtime: &'static str,
    pub var: &'static str,
    /// The CLI's own home under HOME when `var` is unset (the bot's own, whose `links` the turn's home gets).
    pub default: &'static str,
    /// The sign-in file, relative to the home.
    pub file: &'static str,
    /// Linked from the bot's own home when there, `keep` made there first so what the CLI writes in them stays.
    links: &'static [&'static str],
    keep: &'static [&'static str],
    /// Variables that would sign the CLI in some other way than the file: taken off its own process only.
    pub strip: &'static [&'static str],
    /// Set on the CLI's own process only (the bot's tools inside it inherit it), never in the turn's environment.
    pub env: &'static [(&'static str, &'static str)],
}

pub const TURN_HOMES: &[TurnHome] = &[TurnHome {
    runtime: "antigravity",
    var: "GEMINI_HOME",
    default: ".gemini",
    file: AGY_TOKEN,
    links: &[
        "AGENTS.md",
        "GEMINI.md",
        "config/skills.json",
        "config/skills",
        "antigravity-cli/skills",
        "antigravity-cli/conversations",
        "antigravity-cli/brain",
        "antigravity-cli/annotations",
        "antigravity-cli/implicit",
        "antigravity-cli/knowledge",
    ],
    keep: &["antigravity-cli/conversations", "antigravity-cli/brain"],
    strip: &[
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_QUOTA_PROJECT",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GCLOUD_PROJECT",
        "CLOUDSDK_CORE_PROJECT",
        // Signs agy in with the computer's Application Default Credentials.
        "AGY_ADC_AUTH",
    ],
    env: &[OVER_SSH],
}];

pub fn turn_home(runtime: &str) -> Option<&'static TurnHome> {
    TURN_HOMES.iter().find(|t| t.runtime == runtime)
}

impl TurnHome {
    /// The CLI's own home in `env`: `var`, else `default` under HOME.
    pub fn own(&self, env: &Env) -> Option<PathBuf> {
        match env.get(self.var).filter(|v| !v.is_empty()) {
            Some(v) => Some(v.into()),
            None => env
                .get("HOME")
                .filter(|h| !h.is_empty())
                .map(|h| Path::new(h).join(self.default)),
        }
    }

    /// The sign-in a plan turn's CLI is about to run on in `home`: a plain file (never through a link), no larger than
    /// a subscription, holding agy's `{"token": {...}}` with a token in it. Err says why there is none.
    pub fn sign_in(&self, home: &str) -> Result<String, String> {
        let text = read_codex_auth(&Path::new(home).join(self.file))
            .ok_or("the plan's sign-in is not in place")?;
        let v: Value =
            serde_json::from_str(&text).map_err(|_| "the plan's sign-in is unreadable")?;
        if ["access_token", "refresh_token"]
            .iter()
            .any(|k| v["token"][k].as_str().is_some_and(|t| !t.is_empty()))
        {
            Ok(text)
        } else {
            Err("the plan's sign-in holds no token".into())
        }
    }

    /// Link `links` of the bot's own home `own` into the turn's `home` (under `root`), each folder on the way opened
    /// without following a link (`Folder`). Best effort: a turn whose links fail still runs.
    fn link(&self, root: &Path, home: &Path, own: &Path) {
        if own == home {
            return;
        }
        if let Some(up) = own.parent() {
            for rel in self.keep {
                let _ = Folder::open(up, &own.join(rel), true);
            }
        }
        for rel in self.links {
            let target = own.join(rel);
            let link = home.join(rel);
            let (Some(parent), Some(name)) = (link.parent(), link.file_name()) else {
                continue;
            };
            if std::fs::symlink_metadata(&target).is_err() {
                continue;
            }
            if let (Ok(dir), Ok(name)) = (Folder::open(root, parent, true), c_name(name)) {
                if dir.kind(&name).is_none() {
                    let _ = dir.symlink(&name, &target);
                }
            }
        }
    }
}
/// Claude Code's sign-in file in its config directory.
pub const CREDENTIALS: &str = ".credentials.json";
/// What stays the subscription home's own in a full Claude login's config directory: everything else is linked to the
/// turn's own `~/.claude`.
const OWN: &[&str] = &[CREDENTIALS, ".claude.json"];
/// Where a full Claude login keeps one config directory per turn: `subscriptions/<id>/turns/<turn>/claude`. Two turns
/// on one plan here (two bots, two HOMEs) each link their own `~/.claude` in, so neither repoints the other's.
const TURNS: &str = "turns";
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
    attempt_only: bool,
    /// The variable an environment subscription is set in.
    pub env: Option<String>,
    /// The CLI's own home (CODEX_HOME) for a home subscription, and its sign-in file there.
    pub home: Option<PathBuf>,
    /// The variable the home is set in when it is not the runtime's profile home (a full Claude login:
    /// CLAUDE_CONFIG_DIR, while HOME stays the turn's).
    pub home_env: Option<String>,
    pub file: Option<PathBuf>,
    /// Where `home` is made from, one folder at a time (`Folder::open`): the runner's own state.
    root: PathBuf,
    /// As the server handed it.
    value: String,
    /// What the file held when this turn put it in place (`start`).
    pub written: String,
    /// What Codex left in the file when the turn let go of it, when it refreshed the login (`release`).
    pub refreshed: Option<String>,
    /// The turn holds the home's sign-in file now: from its runtime's `start` to `release`.
    pub held: bool,
}

/// The conversation key's suffix every subscription whose threads live in the turn's own home shares (`binding`).
pub const SHARED_BINDING: &str = ":subscriptions";

impl Delivered {
    /// The conversation key's suffix: a thread begun on the computer's own login is never resumed on a subscription,
    /// nor the other way round. A subscription whose threads live in the turn's own home (an environment one, or a full
    /// Claude login, whose config home links the turn's `projects`) shares one suffix with every other such: a move
    /// between plans resumes the same thread, as Claude Code resumes a session begun under another account, of another
    /// organization too (lanes::PLAN_SWITCH_RESUME). A Codex home's threads live in that home, so its suffix names it.
    pub fn binding(&self) -> Option<String> {
        Some(if self.shares_threads() {
            SHARED_BINDING.into()
        } else {
            self.own_binding()
        })
    }

    /// The suffix a runner before the shared one saved this subscription's threads under: a conversation that has
    /// nothing under the shared one takes its thread from there, once.
    pub fn legacy_binding(&self) -> Option<String> {
        self.shares_threads().then(|| self.own_binding())
    }

    fn own_binding(&self) -> String {
        format!(":subscription:{}", self.id)
    }

    fn shares_threads(&self) -> bool {
        self.env.is_some() || self.home_env.is_some()
    }

    /// A plan login in a home made for each turn (TURN_HOMES), with its runtime's row.
    fn turn_home(&self) -> Option<&'static TurnHome> {
        turn_home(&self.runtime).filter(|_| self.home.is_some())
    }

    /// A plan login in its own CLI home can sign in shell commands during another runtime's turn. Current hubs send
    /// the agent's shell payer, and older hubs send the computer's login. API keys pay only for their own runtime.
    fn for_shell(&self) -> bool {
        self.home.is_some()
            && self.turn_home().is_none()
            && self.billing == "subscription"
            && crate::profiles::home_var(&self.runtime).is_some_and(|v| v != "HOME")
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
    /// A plan's sign-in file its CLI refreshes in place (TURN_HOMES), with how it looked when last read
    /// (`notice_refreshes`).
    watch: Option<(PathBuf, Option<Stamp>)>,
}

/// A file's inode, size and change time: what tells a rewrite of it without reading it.
type Stamp = (u64, u64, i64, i64);

fn stamp(path: &Path) -> Option<Stamp> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::symlink_metadata(path).ok()?;
    Some((m.ino(), m.size(), m.mtime(), m.mtime_nsec()))
}

/// Mask, in every running turn's output from now on, the tokens a plan's CLI wrote when it refreshed its sign-in during
/// a turn (TURN_HOMES): called before what a turn said is kept (drive.rs, capture.rs), so a refreshed token is never
/// kept or sent unmasked. One stat per plan turn running here when nothing changed; a changed file is read bounded and
/// never through a link.
pub fn notice_refreshes() {
    let mut homes = homes();
    for live in homes.live.values_mut() {
        let Some((file, seen)) = &mut live.watch else {
            continue;
        };
        let now = stamp(file);
        if now == *seen {
            continue;
        }
        *seen = now;
        if let Some(text) = read_codex_auth(file) {
            for secret in secrets_in(&text) {
                crate::redact::hide_everywhere(&secret, false);
            }
        }
    }
}

fn homes() -> MutexGuard<'static, Homes> {
    static HOMES: OnceLock<Mutex<Homes>> = OnceLock::new();
    HOMES
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn safe_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 80 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

fn billing(value: &Value) -> &'static str {
    if value.as_str() == Some("api") {
        "api"
    } else {
        "subscription"
    }
}

/// What to mask for a sign-in file: the text whole and every string in it long enough to be a token or key (not
/// timestamps), at any depth.
pub fn secrets_in(text: &str) -> Vec<String> {
    fn walk(v: &Value, out: &mut Vec<String>) {
        match v {
            Value::String(s) => {
                let stamp = s.len() <= 40
                    && s.as_bytes()
                        .get(..5)
                        .is_some_and(|b| b[..4].iter().all(u8::is_ascii_digit) && b[4] == b'-');
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

/// What to mask for a full Claude login (its whole value or a turn's `.credentials.json`): the text whole and its
/// tokens, not the account's email or plan names.
pub fn claude_secrets(text: &str) -> Vec<String> {
    let mut out = vec![text.to_string()];
    if let Ok(v) = serde_json::from_str::<Value>(text) {
        for key in ["accessToken", "refreshToken"] {
            if let Some(t) = v["claudeAiOauth"][key]
                .as_str()
                .filter(|t| t.len() >= MIN_SECRET)
            {
                out.push(t.to_string());
            }
        }
    }
    out
}

/// Read each subscription the server handed this turn; none is put in place until its runtime starts (`start`).
/// An environment one's runtime's other sign-in variables leave `env` and `keys` (the turn's vault variables) now.
/// Every value joins `values`, the turn's redaction list, for the whole turn: a turn's two processes write to the
/// same checkout and history. A malformed item is left out and said in the runner's log; the turn then runs as it
/// would have without it.
pub fn apply(
    state_dir: &Path,
    items: &Value,
    env: &mut Env,
    values: &mut Vec<String>,
    keys: &mut BTreeSet<String>,
) -> Vec<Delivered> {
    let mut out = vec![];
    for item in items.as_array().into_iter().flatten() {
        let id = text_of(&item["id"]);
        let runtime = text_of(&item["runtime"]);
        let value = text_of(&item["value"]);
        if !safe_id(&id)
            || runtime.is_empty()
            || value.is_empty()
            || value.len() > MAX_VALUE
            || out.iter().any(|d: &Delivered| d.runtime == runtime)
        {
            crate::util::diagnostic(&format!(
                "Tico runner: left a subscription out of this turn: {id} is not usable"
            ));
            continue;
        }
        let mut delivered = Delivered {
            id: id.clone(),
            runtime: runtime.clone(),
            kind: text_of(&item["kind"]),
            revision: item["revision"].as_i64().unwrap_or(0),
            billing: billing(&item["billing"]),
            attempt_only: item["source"] == "attempt",
            env: None,
            home: None,
            home_env: None,
            file: None,
            root: crate::isolation::bot_state(state_dir),
            value: value.clone(),
            written: value.clone(),
            refreshed: None,
            held: false,
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
            let home_env = text_of(&item["home_env"]);
            let overrides = crate::harness_tools::caps(&runtime).profile_home_overrides;
            let per_turn = turn_home(&runtime);
            let known = match per_turn {
                Some(t) => name == t.file && home_env.is_empty(),
                None => {
                    FILES.contains(&name.as_str())
                        && (name == CREDENTIALS) != home_env.is_empty()
                        && (home_env.is_empty() || overrides.contains(&home_env))
                }
            };
            if !known {
                crate::util::diagnostic(&format!(
                    "Tico runner: left a subscription out of this turn: {id} names no known file"
                ));
                continue;
            }
            if let Some(t) = per_turn {
                // Its home's variable, which a later runtime's start takes out of the environment again.
                delivered.home_env = Some(t.var.into());
            } else if !home_env.is_empty() {
                // The CLI ranks a token in its environment above the file.
                for other in crate::harness_tools::api_key_env(&runtime) {
                    env.remove(&other);
                    keys.remove(&other);
                }
                delivered.home_env = Some(home_env);
            }
            let plan = delivered.root.join("subscriptions").join(&id);
            let home = if name == CREDENTIALS || per_turn.is_some() || delivered.attempt_only {
                plan.join(TURNS)
                    .join(uuid::Uuid::new_v4().simple().to_string())
                    .join(&runtime)
            } else {
                plan.join(&runtime)
            };
            values.extend(if name == CREDENTIALS {
                claude_secrets(&value)
            } else {
                secrets_in(&value)
            });
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

/// An automatic candidate is claim metadata until this runner asks for its value at the hop boundary.
pub fn pending(attempt: &Value, runtime: &str) -> bool {
    attempt["subscriptions"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|s| s["activate"] == true && s["runtime"].as_str() == Some(runtime))
}

pub fn available(delivered: &[Delivered], attempt: &Value, runtime: &str) -> bool {
    for_runtime(delivered, runtime).is_some() || pending(attempt, runtime)
}

/// Fetch only immediately before the automatic runtime starts. The server serializes this delivery with login
/// changes: a removed/held/rejected login yields no value; after delivery a change stops this attempt.
/// Older servers send no `activate` metadata, so their original up-front delivery path stays in use.
pub async fn activate(
    url: &str,
    token: &str,
    state_dir: &Path,
    attempt: &Value,
    runtime: &str,
    env: &mut Env,
) -> Result<(Vec<Delivered>, Vec<String>), String> {
    let expected = attempt["subscriptions"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|s| s["activate"] == true && s["runtime"].as_str() == Some(runtime))
        .ok_or("No automatic subscription candidate")?;
    // Runtime names originate in claim metadata, but still must not become arbitrary query text.
    if !safe_id(runtime) {
        return Err("Invalid subscription runtime".into());
    }
    let client = crate::client::Client::new(url, token, 15, 1)?;
    let response = client
        .get(&format!(
            "credential-runtime?subscription_runtime={runtime}"
        ))
        .await
        .map_err(|_| "Could not activate automatic subscription")?;
    // A rolled-back server can return values without clearing the pending fence. Do not start on them.
    if response["subscription_activated"] != true {
        return Err("Server did not activate automatic subscription".into());
    }
    let items: Vec<Value> = response["subscriptions"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|s| s["runtime"] == expected["runtime"] && s["id"] == expected["id"])
        .cloned()
        .collect();
    let mut secrets = vec![];
    let got = apply(
        state_dir,
        &json!(items),
        env,
        &mut secrets,
        &mut BTreeSet::new(),
    );
    if got.is_empty() {
        return Err("Automatic subscription is no longer available".into());
    }
    Ok((got, secrets))
}

/// Sign `runtime` in, in `env`, the environment of its process about to start: the other runtimes' subscriptions
/// leave it (a hop's environment is copied from the turn's first), and their homes are let go, since the host before
/// it has stopped, except a plan login kept in a home (`Delivered::for_shell`), which stays in place with its CLI's
/// home variable set so the turn's own commands can run that CLI signed in. A home subscription's file is put in
/// place now. Returns what joins the turn's redaction list: the file as found in place, and what Codex refreshed in a
/// home let go. A home kept for another runtime's commands that cannot be made leaves its subscription out of the turn,
/// said in the runner's log; this runtime's own fails the turn before it starts, rather than run it on another login.
pub fn start(
    delivered: &mut Vec<Delivered>,
    runtime: &str,
    env: &mut Env,
) -> Result<Vec<String>, String> {
    let mut secrets = vec![];
    for d in delivered
        .iter_mut()
        .filter(|d| d.runtime != runtime && !d.for_shell())
    {
        secrets.extend(release(d));
        if let Some(name) = &d.env {
            if env.get(name) == Some(&d.value) {
                env.remove(name);
            }
        }
        let var = d
            .home_env
            .clone()
            .or_else(|| crate::profiles::home_var(&d.runtime));
        if let (Some(home), Some(var)) = (&d.home, var) {
            if env.get(&var).is_some_and(|v| Path::new(v) == home) {
                env.remove(&var);
            }
        }
    }
    let mut at = 0;
    while at < delivered.len() {
        let d = &mut delivered[at];
        if d.runtime == runtime || !d.for_shell() {
            at += 1;
            continue;
        }
        match hold(d, env) {
            Ok(found) => {
                secrets.extend(found);
                at += 1;
            }
            Err(e) => {
                crate::util::diagnostic(&format!(
                    "Tico runner: left a subscription out of this turn: {e}"
                ));
                delivered.remove(at);
            }
        }
    }
    let Some(at) = delivered.iter().position(|d| d.runtime == runtime) else {
        return Ok(secrets);
    };
    let d = &mut delivered[at];
    if let Some(name) = d.env.clone() {
        for other in crate::harness_tools::api_key_env(runtime) {
            env.remove(&other);
        }
        env.insert(name, d.value.clone());
        return Ok(secrets);
    }
    if d.home.is_none() || d.file.is_none() {
        return Ok(secrets);
    }
    secrets.extend(
        hold(d, env).map_err(|e| format!("The plan's sign-in could not be put in place: {e}"))?,
    );
    Ok(secrets)
}

/// Put a home subscription's file in place for this turn (once: a held home keeps what is there) and point its CLI's
/// home variable at it in `env`, without the variables that CLI reads ahead of that home. Returns the file's secrets
/// when it held something other than what was handed (Codex refreshed it in another turn here).
fn hold(d: &mut Delivered, env: &mut Env) -> Result<Vec<String>, String> {
    let mut secrets = vec![];
    let (Some(home), Some(file)) = (d.home.clone(), d.file.clone()) else {
        return Ok(secrets);
    };
    if !d.held {
        // A path inside the home, checked against TURN_HOMES or FILES when it was handed (`apply`).
        let name = file
            .strip_prefix(&home)
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let written = place(&d.root, &home, &name, &d.value, d.revision)?;
        if written != d.value {
            secrets.extend(if d.home_env.is_some() && d.turn_home().is_none() {
                claude_secrets(&written)
            } else {
                secrets_in(&written)
            });
        }
        d.written = written;
        d.held = true;
    }
    if let Some(t) = d.turn_home() {
        if let Some(live) = homes().live.get_mut(&home) {
            if live.watch.is_none() {
                live.watch = Some((file.clone(), stamp(&file)));
            }
        }
        // What else would sign the CLI in comes off its own process only (hosts/antigravity.rs): the turn's other
        // runtimes, a fallback hop among them, keep the bot's keys.
        if let Some(own) = t.own(env) {
            t.link(&d.root, &home, &own);
        }
        env.insert(t.var.into(), home.display().to_string());
        return Ok(secrets);
    }
    if let Some(var) = d.home_env.clone() {
        for other in crate::harness_tools::api_key_env(&d.runtime) {
            env.remove(&other);
        }
        if let Some(own) = env.get("HOME").filter(|h| !h.is_empty()) {
            link_config(&d.root, &home, &Path::new(own).join(".claude"));
        }
        env.insert(var, home.display().to_string());
        return Ok(secrets);
    }
    if let Some(var) = crate::profiles::home_var(&d.runtime) {
        if d.attempt_only {
            // Codex resumes from the plan's session directory, with a separate auth.json per attempt.
            let plan = home
                .ancestors()
                .nth(3)
                .ok_or("Missing provider home")?
                .join(&d.runtime);
            let sessions = plan.join("sessions");
            Folder::open(&d.root, &sessions, true)
                .map_err(|_| "Cannot create provider sessions")?;
            let dir =
                Folder::open(&d.root, &home, false).map_err(|_| "Cannot link provider sessions")?;
            if dir.kind(c"sessions") != Some(libc::S_IFLNK) {
                dir.symlink(c"sessions", &sessions)
                    .map_err(|_| "Cannot link provider sessions")?;
            }
            for key in crate::harness_tools::api_key_env(&d.runtime) {
                env.remove(&key);
            }
        }
        for key in crate::harness_tools::caps(&d.runtime).profile_home_overrides {
            env.remove(&key);
        }
        env.insert(var, home.display().to_string());
    }
    Ok(secrets)
}

/// A full Claude login's config directory sees the turn's own `~/.claude` (`own`): each entry there but the sign-in
/// files is linked in, `projects` first made so the transcripts stay under HOME (hosts/claude.rs transcript). A link
/// to another folder (the bot's HOME moved) is replaced; what Claude Code made in the home itself is left alone. A
/// fresh home is marked as onboarded, as the computer's own is. Best effort: a turn whose links fail still runs.
fn link_config(root: &Path, home: &Path, own: &Path) {
    if let Some(up) = own.parent() {
        let _ = Folder::open(up, &own.join("projects"), true);
    }
    let Ok(dir) = Folder::open(root, home, false) else {
        return;
    };
    for entry in std::fs::read_dir(own).into_iter().flatten().flatten() {
        let name = entry.file_name();
        let text = name.to_string_lossy();
        if OWN.contains(&text.as_ref()) || text.contains(".tico-") {
            continue;
        }
        let Ok(name) = c_name(&name) else {
            continue;
        };
        match dir.kind(&name) {
            Some(libc::S_IFLNK) => {
                if dir.read_link(&name).is_some_and(|t| t == entry.path()) {
                    continue;
                }
                dir.remove(&name);
            }
            Some(_) => continue,
            None => {}
        }
        let _ = dir.symlink(&name, &entry.path());
    }
    if dir.kind(c".claude.json").is_none() {
        let _ = dir.write(c".claude.json", "{\"hasCompletedOnboarding\":true}");
    }
}

/// The plan a home is of: `subscriptions/<id>/<runtime>`, or a full Claude login's `subscriptions/<id>/turns/<turn>/claude`.
fn plan_of(home: &Path) -> Option<String> {
    let mut up = home.parent()?;
    if up
        .parent()
        .and_then(|p| p.file_name())
        .is_some_and(|n| n == TURNS)
    {
        up = up.parent()?.parent()?;
    }
    Some(up.file_name()?.to_string_lossy().to_string())
}

/// A full Claude login's config directory with a turn running on it: the one CLAUDE_CONFIG_DIR a turn keeps
/// (hosts/claude.rs turn_env).
pub fn is_config_home(path: &str) -> bool {
    let path = Path::new(path);
    path.file_name().is_some_and(|n| n == "claude")
        && homes()
            .live
            .get(path)
            .is_some_and(|_| path.join(CREDENTIALS).is_file())
}

/// Each full Claude login a turn here runs on, with the revision its file has (claude_login.rs asks for newer ones).
/// Each plan once, at the oldest revision any of its turns has.
pub fn live_logins() -> Vec<(String, i64)> {
    let mut out: BTreeMap<String, i64> = BTreeMap::new();
    for (home, live) in homes().live.iter() {
        if !home.join(CREDENTIALS).is_file() {
            continue;
        }
        if let Some(id) = plan_of(home) {
            let at = out.entry(id).or_insert(live.revision);
            *at = (*at).min(live.revision);
        }
    }
    out.into_iter().collect()
}

/// A full Claude login's new access token (`value`, its `.credentials.json` at `revision`): written into every home
/// of that plan a turn here still runs on at an older revision, and masked in every turn's output. Returns how many
/// homes it rewrote.
pub fn rewrite(id: &str, revision: i64, value: &str) -> usize {
    if !safe_id(id) || value.is_empty() || value.len() > MAX_VALUE {
        return 0;
    }
    for secret in claude_secrets(value) {
        crate::redact::hide_everywhere(&secret, false);
    }
    write_logins(id, value, Some(revision))
}

/// A login this computer refreshed and the server has not yet acknowledged: the old access token is revoked already,
/// so every home of the plan gets the new one now, while each keeps the revision it had until the server says which
/// revision this is (`rewrite`), or that it kept another (`stale`).
pub fn renew(id: &str, value: &str) -> usize {
    if !safe_id(id) || value.is_empty() || value.len() > MAX_VALUE {
        return 0;
    }
    for secret in claude_secrets(value) {
        crate::redact::hide_everywhere(&secret, false);
    }
    write_logins(id, value, None)
}

/// The server did not keep this computer's refresh of a plan (a Reconnect came first): every home of it asks for the
/// server's login on the next poll, whatever revision it had.
pub fn stale(id: &str) {
    for (home, live) in homes().live.iter_mut() {
        if plan_of(home).is_some_and(|p| p == id) && home.join(CREDENTIALS).is_file() {
            live.revision = 0;
        }
    }
}

/// Write `value` into each live home of plan `id`: those behind `revision` (and record it), or all of them (None).
fn write_logins(id: &str, value: &str, revision: Option<i64>) -> usize {
    let mut homes = homes();
    let mut done = 0;
    for (home, live) in homes.live.iter_mut() {
        let file = home.join(CREDENTIALS);
        if plan_of(home).is_none_or(|p| p != id)
            || revision.is_some_and(|r| live.revision >= r)
            || !file.is_file()
        {
            continue;
        }
        match write_private(&file, value) {
            Ok(()) => {
                if let Some(r) = revision {
                    live.revision = r;
                }
                done += 1;
            }
            Err(e) => crate::util::diagnostic(&format!(
                "Tico runner: could not renew a Claude plan's sign-in: {e}"
            )),
        }
    }
    done
}

/// Hold `home/name` for one turn. The first turn on a home writes the file: the login Codex refreshed here at this
/// revision, else `value`. A turn joining one already running keeps the file in place (Codex may have refreshed it)
/// unless it brings a newer revision (someone reconnected it). Returns what the file holds now.
fn place(
    root: &Path,
    home: &Path,
    name: &str,
    value: &str,
    revision: i64,
) -> Result<String, String> {
    let mut homes = homes();
    let path = home.join(name);
    let dir = Folder::open(root, path.parent().unwrap_or(home), true)?;
    let leaf = c_name(path.file_name().ok_or_else(refused)?)?;
    let live = homes.live.get(home).map(|l| l.revision);
    let current = match live {
        Some(at) if revision <= at => dir.read(&leaf),
        _ => None,
    };
    let text = match current {
        Some(text) => text,
        None => {
            let text = match homes.kept.get(home) {
                Some((at, kept)) if live.is_none() && *at == revision => kept.clone(),
                _ => value.to_string(),
            };
            dir.write(&leaf, &text)?;
            text
        }
    };
    let entry = homes.live.entry(home.to_path_buf()).or_insert(Live {
        turns: 0,
        revision,
        watch: None,
    });
    entry.turns += 1;
    entry.revision = entry.revision.max(revision);
    Ok(text)
}

fn refused() -> String {
    "a subscription home's folder is not a plain folder".into()
}

fn c_name(name: &OsStr) -> Result<CString, String> {
    CString::new(name.as_bytes()).map_err(|_| refused())
}

/// A folder held open by descriptor. Under isolation the bot user owns the runner's per-turn state (isolation.rs
/// `bot_state`) and may swap any name in it at any moment, so every folder a subscription home is made of is opened
/// one name at a time from the runner's state without following a link, and what is made, read, written, renamed or
/// removed there is named relative to it: a swap after the check cannot send the supervisor's write anywhere else.
struct Folder(OwnedFd);

impl Folder {
    /// `path`, opened without following a link anywhere on the way but root's own (isolation::open_unfollowed).
    fn at(path: &Path) -> Result<Folder, String> {
        crate::isolation::open_unfollowed(path, libc::O_DIRECTORY)
            .map(|f| Folder(f.into()))
            .map_err(|_| refused())
    }

    /// `dir`, under `root`: each folder below `root` opened without following a link, a missing one made (`make`),
    /// 0700 and the bot user's.
    fn open(root: &Path, dir: &Path, make: bool) -> Result<Folder, String> {
        let rest = dir.strip_prefix(root).map_err(|_| refused())?;
        let mut at = Folder::at(root)?;
        for part in rest.components() {
            let std::path::Component::Normal(part) = part else {
                return Err(refused());
            };
            at = at.child(&c_name(part)?, make)?;
        }
        Ok(at)
    }

    fn raw(&self) -> i32 {
        self.0.as_raw_fd()
    }

    /// The folder `name` in this one, never through a link; made when missing (`make`).
    fn child(&self, name: &CStr, make: bool) -> Result<Folder, String> {
        let open =
            || crate::isolation::open_at(self.raw(), name, libc::O_RDONLY | libc::O_DIRECTORY);
        if let Some(fd) = open() {
            return Ok(Folder(fd));
        }
        if !make || std::io::Error::last_os_error().kind() != std::io::ErrorKind::NotFound {
            return Err(refused());
        }
        // SAFETY: mkdirat on a descriptor this process holds and a NUL-terminated name.
        if unsafe { libc::mkdirat(self.raw(), name.as_ptr(), 0o700) } != 0 {
            let e = std::io::Error::last_os_error();
            if e.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(format!("cannot make a subscription home: {e}"));
            }
        }
        let made = std::fs::File::from(open().ok_or_else(refused)?);
        crate::isolation::chown_file(&made).map_err(|e| e.to_string())?;
        Ok(Folder(made.into()))
    }

    /// The type of what `name` is here (`S_IFDIR`, `S_IFLNK`...), never following a link; None when nothing is.
    fn kind(&self, name: &CStr) -> Option<libc::mode_t> {
        let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: fstatat on a held descriptor and a NUL-terminated name, into an owned buffer.
        let found = unsafe {
            libc::fstatat(
                self.raw(),
                name.as_ptr(),
                info.as_mut_ptr(),
                libc::AT_SYMLINK_NOFOLLOW,
            )
        } == 0;
        found.then(|| unsafe { info.assume_init() }.st_mode & libc::S_IFMT)
    }

    /// What the file `name` holds: a plain file, never read through a link, bounded as a subscription is.
    fn read(&self, name: &CStr) -> Option<String> {
        let fd = crate::isolation::open_at(self.raw(), name, libc::O_RDONLY | libc::O_NONBLOCK)?;
        read_bounded(std::fs::File::from(fd))
    }

    /// Write `value` as the file `name` (0600, the bot user's): staged under a name of its own in this folder and
    /// renamed over it, so the CLI never reads half a file and nothing is written through a link.
    fn write(&self, name: &CStr, value: &str) -> Result<(), String> {
        let staged = Path::new(OsStr::from_bytes(name.to_bytes()))
            .with_extension(format!("tico-{}", uuid::Uuid::new_v4().simple()));
        let staged = c_name(staged.as_os_str())?;
        // SAFETY: openat on a held descriptor and a NUL-terminated name; the descriptor gets one owner below.
        let fd = unsafe {
            libc::openat(
                self.raw(),
                staged.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600 as libc::c_uint,
            )
        };
        if fd < 0 {
            return Err(format!(
                "cannot write a subscription file: {}",
                std::io::Error::last_os_error()
            ));
        }
        let mut f = unsafe { std::fs::File::from_raw_fd(fd) };
        let written = crate::isolation::chown_file(&f)
            .map_err(|e| e.to_string())
            .and_then(|_| f.write_all(value.as_bytes()).map_err(|e| e.to_string()))
            .and_then(|_| {
                // SAFETY: renameat within one held descriptor, NUL-terminated names.
                if unsafe { libc::renameat(self.raw(), staged.as_ptr(), self.raw(), name.as_ptr()) }
                    == 0
                {
                    Ok(())
                } else {
                    Err(std::io::Error::last_os_error().to_string())
                }
            });
        drop(f);
        if written.is_err() {
            // SAFETY: unlinkat of the staged name in a held descriptor.
            unsafe { libc::unlinkat(self.raw(), staged.as_ptr(), 0) };
        }
        written
    }

    fn symlink(&self, name: &CStr, target: &Path) -> Result<(), String> {
        let target = c_name(target.as_os_str())?;
        // SAFETY: symlinkat into a held descriptor with NUL-terminated names.
        if unsafe { libc::symlinkat(target.as_ptr(), self.raw(), name.as_ptr()) } == 0 {
            Ok(())
        } else {
            Err(std::io::Error::last_os_error().to_string())
        }
    }

    fn read_link(&self, name: &CStr) -> Option<PathBuf> {
        let mut target = vec![0u8; libc::PATH_MAX as usize];
        // SAFETY: readlinkat on a held descriptor and a NUL-terminated name into an owned buffer.
        let n = unsafe {
            libc::readlinkat(
                self.raw(),
                name.as_ptr(),
                target.as_mut_ptr().cast(),
                target.len(),
            )
        };
        (n > 0 && (n as usize) < target.len())
            .then(|| PathBuf::from(OsStr::from_bytes(&target[..n as usize])))
    }

    /// The names in this folder.
    fn names(&self) -> Vec<CString> {
        let mut out = vec![];
        // SAFETY: fdopendir takes a duplicate this process owns (closed by closedir), rewound since it shares the
        // held descriptor's position; readdir's entries are copied out before the next call.
        unsafe {
            let dup = libc::fcntl(self.raw(), libc::F_DUPFD_CLOEXEC, 0);
            if dup < 0 {
                return out;
            }
            let stream = libc::fdopendir(dup);
            if stream.is_null() {
                libc::close(dup);
                return out;
            }
            libc::rewinddir(stream);
            loop {
                let entry = libc::readdir(stream);
                if entry.is_null() {
                    break;
                }
                let name = CStr::from_ptr((*entry).d_name.as_ptr());
                if !matches!(name.to_bytes(), b"." | b"..") {
                    out.push(name.to_owned());
                }
            }
            libc::closedir(stream);
        }
        out
    }

    /// Remove `name` here: a file or link (never what a link points at), or a folder with all that is in it.
    fn remove(&self, name: &CStr) {
        self.remove_within(name, 32);
    }

    fn remove_within(&self, name: &CStr, depth: usize) {
        if self.kind(name) == Some(libc::S_IFDIR) {
            if let (Ok(dir), true) = (self.child(name, false), depth > 0) {
                for inner in dir.names() {
                    dir.remove_within(&inner, depth - 1);
                }
            }
            // SAFETY: unlinkat on a held descriptor and a NUL-terminated name.
            unsafe { libc::unlinkat(self.raw(), name.as_ptr(), libc::AT_REMOVEDIR) };
        } else {
            // SAFETY: as above.
            unsafe { libc::unlinkat(self.raw(), name.as_ptr(), 0) };
        }
    }
}

/// Write `value` as `path`, its folder opened without following a link (`Folder::at`).
pub(crate) fn write_private(path: &Path, value: &str) -> Result<(), String> {
    let (Some(folder), Some(name)) = (path.parent(), path.file_name()) else {
        return Err(refused());
    };
    Folder::at(folder)?.write(&c_name(name)?, value)
}

/// Remove the sign-in files from one subscription home (and what a write cut short left), keeping its sessions.
fn clear(root: &Path, home: &Path) {
    if let Ok(dir) = Folder::open(root, home, false) {
        clear_in(&dir);
    }
}

fn clear_in(dir: &Folder) {
    for entry in dir.names() {
        let name = entry.to_string_lossy();
        let staged = FILES.iter().any(|f| {
            name.strip_prefix(f.trim_end_matches(".json"))
                .is_some_and(|rest| rest.starts_with(".tico-"))
        });
        if FILES.contains(&name.as_ref()) || staged || name == LEGACY_REVISION_FILE {
            dir.remove(&entry);
        }
    }
}

/// At the runner's start, before any turn: no turn holds a home, so a sign-in file left in one (the runner stopped
/// mid-turn, or an older runner kept it) is removed.
pub fn sweep(state_dir: &Path) {
    let state = crate::isolation::bot_state(state_dir);
    let Ok(root) = Folder::open(&state, &state.join("subscriptions"), false) else {
        return;
    };
    for sub in root.names() {
        let Ok(sub) = root.child(&sub, false) else {
            continue;
        };
        for home in sub.names() {
            if home.as_bytes() == TURNS.as_bytes() {
                sub.remove(&home);
            } else if let Ok(home) = sub.child(&home, false) {
                clear_in(&home);
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
    let Some(home) = d.home.clone().filter(|_| d.held) else {
        return secrets;
    };
    d.held = false;
    let mut homes = homes();
    if d.turn_home().is_some() {
        // A plan turn's CLI refreshes its access token in the file: masked for the turn, never kept or sent back.
        if let Some(now) = d.file.as_deref().and_then(read_codex_auth) {
            if now != d.written {
                secrets.extend(secrets_in(&now));
            }
        }
    } else if let Some(now) = changed(d) {
        secrets.extend(secrets_in(&now));
        if !d.attempt_only
            && homes
                .live
                .get(&home)
                .is_some_and(|l| l.revision == d.revision)
        {
            homes.kept.insert(home.clone(), (d.revision, now.clone()));
        }
        d.refreshed = Some(now);
    }
    match homes.live.get_mut(&home) {
        Some(live) if live.turns > 1 => live.turns -= 1,
        _ => {
            homes.live.remove(&home);
            // Through descriptors from the runner's state (`Folder`): a link put in place of any folder on the way
            // is removed, never followed.
            clear(&d.root, &home);
            if let Some(turn) = turn_dir(&home) {
                // A turn's own config directory: its links go with it (never what they point at).
                if let (Some(up), Some(name)) = (turn.parent(), turn.file_name()) {
                    if let (Ok(dir), Ok(name)) = (Folder::open(&d.root, up, false), c_name(name)) {
                        dir.remove(&name);
                    }
                }
            }
        }
    }
    secrets
}

/// `subscriptions/<id>/turns/<turn>` of a full Claude login's home; None for any other home.
fn turn_dir(home: &Path) -> Option<&Path> {
    let turn = home.parent()?;
    turn.parent()
        .and_then(|p| p.file_name())
        .is_some_and(|n| n == TURNS)
        .then_some(turn)
}

pub fn for_runtime<'a>(delivered: &'a [Delivered], runtime: &str) -> Option<&'a Delivered> {
    delivered.iter().find(|d| d.runtime == runtime)
}

/// The home of `runtime`'s plan in this turn when its sign-in lives in a home made for the turn (TURN_HOMES): the
/// host runs that CLI on nothing but the sign-in there (hosts::Settings::plan_home), every time it starts it.
pub fn plan_home(delivered: &[Delivered], runtime: &str) -> Option<String> {
    for_runtime(delivered, runtime)
        .filter(|d| d.turn_home().is_some())
        .and_then(|d| d.home.as_ref())
        .map(|h| h.display().to_string())
}

/// The file as Codex left it, when it refreshed a ChatGPT login since the turn began; None when it is as written.
fn changed(d: &Delivered) -> Option<String> {
    if d.kind != "codex_login" {
        return None;
    }
    let now = read_codex_auth(d.file.as_ref()?)?;
    (now != d.written).then_some(now)
}

/// The auth file is writable by the bot. Refuse special files and bound the read.
pub fn read_codex_auth(path: &Path) -> Option<String> {
    read_bounded(crate::isolation::open_unfollowed(path, libc::O_NONBLOCK).ok()?)
}

/// A plain file's text, no larger than a subscription; None for anything else or nothing.
fn read_bounded(file: std::fs::File) -> Option<String> {
    if !file.metadata().ok()?.is_file() {
        return None;
    }
    let mut auth = String::new();
    file.take(MAX_VALUE as u64 + 1)
        .read_to_string(&mut auth)
        .ok()?;
    (!auth.trim().is_empty() && auth.len() <= MAX_VALUE).then_some(auth)
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
        match client
            .post(
                "runner-model-subscription-refresh",
                &body,
                Some(&format!("subscription:{attempt}:{}", d.id)),
            )
            .await
        {
            Ok(answer) => {
                let Some(home) = &d.home else { continue };
                let mut homes = homes();
                if homes.kept.get(home).is_none_or(|(_, kept)| kept != value) {
                    continue; // another turn here kept a later one
                }
                match (
                    answer["stored"].as_bool() == Some(true),
                    answer["revision"].as_i64(),
                ) {
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
            Err(e) => crate::util::diagnostic(&format!(
                "Tico runner: could not send a refreshed sign-in back: {}",
                e.detail
            )),
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
    fn attempt_only_codex_uses_a_private_auth_file_and_never_keeps_it() {
        let state = dir();
        let items = |token| {
            json!([{
                "id": "plan", "runtime": "codex", "kind": "codex_login", "billing": "subscription",
                "file": "auth.json", "revision": 1, "source": "attempt", "value": token,
            }])
        };
        let mut first = turn(&state, &items("{\"tokens\":\"attempt one\"}"), "codex");
        // The hub can also deliver the agent's Codex login for the shell of a Claude turn.
        let mut second = turn(&state, &items("{\"tokens\":\"attempt two\"}"), "claude");
        let one = first[0].file.clone().unwrap();
        let two = second[0].file.clone().unwrap();
        assert_ne!(one, two);
        assert!(std::fs::read_to_string(&one)
            .unwrap()
            .contains("attempt one"));
        assert!(std::fs::read_to_string(&two)
            .unwrap()
            .contains("attempt two"));
        std::fs::write(&one, "{\"tokens\":\"refreshed\"}").unwrap();
        finish(&mut first);
        assert!(!one.exists() && two.exists());
        assert!(first[0].refreshed.is_some());
        assert!(!homes().kept.contains_key(first[0].home.as_ref().unwrap()));
        finish(&mut second);
        assert!(!two.exists());
        std::fs::remove_dir_all(state).unwrap();
    }

    /// A turn handed `items` whose `runtime` process starts.
    fn turn(state: &Path, items: &Value, runtime: &str) -> Vec<Delivered> {
        let mut got = apply(
            state,
            items,
            &mut Env::new(),
            &mut vec![],
            &mut BTreeSet::new(),
        );
        start(&mut got, runtime, &mut Env::new()).unwrap();
        got
    }

    #[test]
    fn a_token_replaces_every_other_sign_in_variable_of_its_runtime_only() {
        let state = dir();
        let mut env: Env = [
            ("ANTHROPIC_API_KEY".into(), "sk-ant-computer".into()),
            ("ACME_KEY".into(), "acme".into()),
        ]
        .into();
        let (mut values, mut keys) = (vec![], BTreeSet::from(["ANTHROPIC_API_KEY".to_string()]));
        let items = json!([{"id": "s1", "runtime": "claude", "kind": "claude_token", "env": "CLAUDE_CODE_OAUTH_TOKEN",
                            "value": "sk-ant-oat01-synthetic", "revision": 1, "billing": "subscription"}]);
        let mut got = apply(&state, &items, &mut env, &mut values, &mut keys);
        assert_eq!(got.len(), 1);
        assert!(
            !env.contains_key("ANTHROPIC_API_KEY") && !env.contains_key("CLAUDE_CODE_OAUTH_TOKEN"),
            "{env:?}"
        );
        assert!(!keys.contains("ANTHROPIC_API_KEY"));
        assert_eq!(values, vec!["sk-ant-oat01-synthetic".to_string()]);
        // Set when Claude starts.
        assert!(start(&mut got, "claude", &mut env).unwrap().is_empty());
        assert_eq!(
            env.get("CLAUDE_CODE_OAUTH_TOKEN").map(String::as_str),
            Some("sk-ant-oat01-synthetic")
        );
        assert_eq!(env["ACME_KEY"], "acme");
        assert_eq!(got[0].billing, "subscription");
        // Claude keeps its threads in the turn's own home, so every plan shares one key; the plan's own is the old one.
        assert_eq!(got[0].binding().as_deref(), Some(SHARED_BINDING));
        assert_eq!(got[0].legacy_binding().as_deref(), Some(":subscription:s1"));
        // A variable that does not sign the runtime in is never set.
        let mut env2 = Env::new();
        let bad = json!([{"id": "s2", "runtime": "claude", "env": "PATH", "value": "/evil", "revision": 1}]);
        assert!(apply(&state, &bad, &mut env2, &mut vec![], &mut BTreeSet::new()).is_empty());
        assert!(!env2.contains_key("PATH"));
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn two_turns_on_one_full_claude_login_keep_their_own_config_and_each_gets_the_new_token() {
        let state = dir();
        let id = format!("cl{}", uuid::Uuid::new_v4().simple());
        let file = |token: &str| json!({"claudeAiOauth": {"accessToken": token}}).to_string();
        let item = json!([{"id": id, "runtime": "claude", "kind": "claude_login", "file": CREDENTIALS,
                           "home_env": "CLAUDE_CONFIG_DIR", "value": file("sk-ant-oat01-first-synthetic"),
                           "revision": 3, "billing": "subscription"}]);
        // Two bots, each with its own HOME, on one plan at once.
        let mut turns = vec![];
        for bot in ["ana", "bo"] {
            let own = state.join(bot).join(".claude");
            std::fs::create_dir_all(own.join(bot)).unwrap();
            let mut env: Env = [("HOME".into(), state.join(bot).display().to_string())].into();
            let mut got = apply(&state, &item, &mut env, &mut vec![], &mut BTreeSet::new());
            start(&mut got, "claude", &mut env).unwrap();
            let home = PathBuf::from(&env["CLAUDE_CONFIG_DIR"]);
            assert!(is_config_home(&env["CLAUDE_CONFIG_DIR"]));
            // Its transcripts are the turn's own, so a move to or from another Claude plan resumes the thread.
            assert_eq!(got[0].binding().as_deref(), Some(SHARED_BINDING));
            assert_eq!(
                std::fs::read_link(home.join("projects")).unwrap(),
                own.join("projects")
            );
            assert_eq!(std::fs::read_link(home.join(bot)).unwrap(), own.join(bot));
            turns.push((got, home));
        }
        let (ana, bo) = (turns[0].1.clone(), turns[1].1.clone());
        assert_ne!(ana, bo);
        // Neither repointed the other's links.
        assert!(std::fs::read_link(ana.join("projects"))
            .unwrap()
            .starts_with(state.join("ana")));
        assert!(!bo.join("ana").exists() && !ana.join("bo").exists());
        assert_eq!(
            live_logins()
                .into_iter()
                .filter(|(p, _)| *p == id)
                .collect::<Vec<_>>(),
            vec![(id.clone(), 3)]
        );
        let read = |home: &Path| std::fs::read_to_string(home.join(CREDENTIALS)).unwrap();
        // Refreshed here: both files at once, each still at its revision until the server says which this is.
        assert_eq!(renew(&id, &file("sk-ant-oat01-second-synthetic")), 2);
        assert!(read(&ana).contains("second") && read(&bo).contains("second"));
        assert!(live_logins().contains(&(id.clone(), 3)));
        assert_eq!(rewrite(&id, 4, &file("sk-ant-oat01-second-synthetic")), 2);
        assert!(live_logins().contains(&(id.clone(), 4)));
        // A refresh the server did not keep: every home asks for the server's login again.
        stale(&id);
        assert!(live_logins().contains(&(id.clone(), 0)));
        assert_eq!(rewrite(&id, 5, &file("sk-ant-oat01-third-synthetic")), 2);
        assert!(read(&ana).contains("third") && read(&bo).contains("third"));
        // A finished turn's config directory goes, never what its links point at.
        for (mut got, home) in turns {
            finish(&mut got);
            assert!(!home.exists());
        }
        assert!(
            state.join("ana/.claude/projects").is_dir() && state.join("bo/.claude/bo").is_dir()
        );
        assert!(!live_logins().iter().any(|(p, _)| *p == id));
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn a_chatgpt_login_gets_its_own_home_and_its_file_lasts_only_while_a_turn_holds_it() {
        let state = dir();
        let item = |rev: i64, value: &str| {
            json!([{"id": "s3", "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                                                    "value": value, "revision": rev, "billing": "subscription"}])
        };
        let mut env: Env = [("CODEX_HOME".into(), "/computer/.codex".into())].into();
        let mut got = apply(
            &state,
            &item(1, "{\"tokens\":1}"),
            &mut env,
            &mut vec![],
            &mut BTreeSet::new(),
        );
        let home = got[0].home.clone().unwrap();
        assert!(home.starts_with(&state) && home.ends_with("subscriptions/s3/codex"));
        let file = home.join("auth.json");
        assert!(!file.exists(), "written before Codex starts");
        start(&mut got, "codex", &mut env).unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "{\"tokens\":1}");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&file).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(env["CODEX_HOME"], home.display().to_string());
        assert_eq!(got[0].binding().as_deref(), Some(":subscription:s3"));
        assert_eq!(got[0].legacy_binding(), None);
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
        assert!(
            finish(&mut got).is_empty(),
            "a second finish changes nothing"
        );
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
        assert_eq!(
            std::fs::read_to_string(moved[0].file.as_ref().unwrap()).unwrap(),
            "{\"tokens\":3}"
        );
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
        assert!(apply(
            &state,
            &escape,
            &mut Env::new(),
            &mut vec![],
            &mut BTreeSet::new()
        )
        .is_empty());
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn each_process_gets_its_runtimes_subscription_and_every_turn_the_chatgpt_login_for_its_commands(
    ) {
        let state = dir();
        let token = "sk-ant-oat01-synthetic-primary";
        let login =
            r#"{"tokens":{"refresh_token":"rt_synthetic_fallback_cccccccc","account_id":"acct"}}"#;
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
        start(&mut got, "claude", &mut env).unwrap();
        assert_eq!(env["CLAUDE_CODE_OAUTH_TOKEN"], token);
        // The ChatGPT login signs `codex` in for Claude's own commands (`codex exec` in its shell), in its own home.
        assert_eq!(env["CODEX_HOME"], home.display().to_string());
        assert_eq!(std::fs::read_to_string(&file).unwrap(), login);
        // No local login and no named fallback: the handed subscription wins before Gateway.
        let config =
            json!({"runtime": "claude", "harness": "claude", "model": "claude-sonnet-5-5"});
        let choices = vec![
            json!({"runtime": "codex", "model": "gpt-6.1-sol"}),
            json!({"runtime": "pi", "model": "tico-medium-1.0"}),
        ];
        let ready = |rt: &str| for_runtime(&got, rt).is_some();
        let chosen = crate::fallback::default_choice(&config, &choices, &env, ready).unwrap();
        assert_eq!(chosen.runtime, "codex");
        assert!(chosen.default);
        assert!(crate::fallback::default_choice(&config, &choices, &env, |_| false).is_none());
        // The hop: its environment is copied from the turn's; it gets the Codex home and loses Claude's token.
        let mut hop = env.clone();
        start(&mut got, "codex", &mut hop).unwrap();
        assert_eq!(hop["CODEX_HOME"], home.display().to_string());
        assert_eq!(std::fs::read_to_string(&file).unwrap(), login);
        assert!(!hop.contains_key("CLAUDE_CODE_OAUTH_TOKEN"), "{hop:?}");
        finish(&mut got);
        assert!(!file.exists());

        // The other way round: Codex first, Claude its fallback. The Codex home stays for Claude's commands; what Codex
        // refreshed goes back when the turn ends.
        let mut env = Env::new();
        let mut got = apply(&state, &items, &mut env, &mut vec![], &mut BTreeSet::new());
        start(&mut got, "codex", &mut env).unwrap();
        assert!(file.exists() && !env.contains_key("CLAUDE_CODE_OAUTH_TOKEN"));
        std::fs::write(
            &file,
            r#"{"tokens":{"refresh_token":"rt_synthetic_refreshed_dddddddd","account_id":"acct"}}"#,
        )
        .unwrap();
        let mut hop = env.clone();
        start(&mut got, "claude", &mut hop).unwrap();
        assert!(got[1].held && file.exists());
        assert_eq!(hop["CODEX_HOME"], home.display().to_string());
        assert_eq!(hop["CLAUDE_CODE_OAUTH_TOKEN"], token);
        let fresh = finish(&mut got);
        assert!(
            fresh.iter().any(|s| s == "rt_synthetic_refreshed_dddddddd"),
            "the refresh was not masked: {fresh:?}"
        );
        assert!(got[1].refreshed.is_some() && !got[1].held);
        assert!(!file.exists(), "the Codex login stayed after the turn");
        assert!(
            finish(&mut got).is_empty(),
            "a home let go is not let go twice"
        );
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn an_openai_key_never_signs_in_another_runtimes_commands() {
        let state = dir();
        let items = json!([
            {"id": "s11", "runtime": "codex", "kind": "openai_key", "file": "auth.json", "billing": "api", "source": "attempt",
             "value": r#"{"OPENAI_API_KEY":"sk-synthetic-openai-key-eeeeeeee"}"#, "revision": 1},
        ]);
        let mut env = Env::new();
        let mut got = apply(&state, &items, &mut env, &mut vec![], &mut BTreeSet::new());
        let file = got[0].file.clone().unwrap();
        start(&mut got, "claude", &mut env).unwrap();
        assert!(!file.exists() && !env.contains_key("CODEX_HOME"), "{env:?}");
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[tokio::test]
    async fn automatic_candidates_fetch_only_at_the_hop_and_removed_values_never_start() {
        let state = dir();
        let attempt =
            json!({"subscriptions": [{"id":"auto-login", "runtime":"codex", "activate":true}]});
        let mut env = Env::new();
        let injected_runtime = "runtime&other=value";
        let injected = json!({"subscriptions": [{"id":"auto-login", "runtime":injected_runtime, "activate":true}]});
        assert_eq!(
            activate(
                "http://127.0.0.1:1",
                "run-token",
                &state,
                &injected,
                injected_runtime,
                &mut env
            )
            .await
            .unwrap_err(),
            "Invalid subscription runtime"
        );
        let config = json!({"runtime":"claude", "harness":"claude", "model":"primary"});
        let choices = vec![json!({"runtime":"codex", "harness":"codex", "model":"fallback"})];
        let choice = crate::fallback::default_choice(&config, &choices, &env, |rt| {
            available(&[], &attempt, rt)
        })
        .unwrap();
        assert_eq!(choice.runtime, "codex");
        assert!(!state
            .join("subscriptions/auto-login/codex/auth.json")
            .exists());
        let reply = json!({"subscription_activated": true, "subscriptions": [
            {"id":"auto-login", "runtime":"codex", "kind":"codex_login", "file":"auth.json", "revision":2,
             "value":"{\"tokens\":{\"refresh_token\":\"synthetic-long-refresh-token\"}}"},
            {"id":"primary-login", "runtime":"claude", "env":"CLAUDE_CODE_OAUTH_TOKEN", "value":"primary-secret"}
        ]}).to_string();
        let (url, seen) =
            crate::client::tests::serve(vec![(200, Box::leak(reply.clone().into_boxed_str()))])
                .await;
        let (mut given, secrets) = activate(&url, "run-token", &state, &attempt, "codex", &mut env)
            .await
            .unwrap();
        assert_eq!(given.len(), 1);
        assert_eq!(given[0].revision, 2);
        assert!(secrets.iter().any(|s| s == "synthetic-long-refresh-token"));
        assert!(!env.contains_key("CLAUDE_CODE_OAUTH_TOKEN"));
        assert!(
            !given[0].file.as_ref().unwrap().exists(),
            "fetching never places the file"
        );
        assert!(seen.lock().unwrap()[0].contains("credential-runtime?subscription_runtime=codex"));
        start(&mut given, "codex", &mut env).unwrap();
        assert!(given[0].file.as_ref().unwrap().exists());
        finish(&mut given);
        let (url, _) = crate::client::tests::serve(vec![(200, "{\"credentials\":[]}")]).await;
        assert!(
            activate(&url, "run-token", &state, &attempt, "codex", &mut env)
                .await
                .is_err()
        );
        let mut rolled_back: Value = serde_json::from_str(&reply).unwrap();
        rolled_back
            .as_object_mut()
            .unwrap()
            .remove("subscription_activated");
        let (url, _) = crate::client::tests::serve(vec![(
            200,
            Box::leak(rolled_back.to_string().into_boxed_str()),
        )])
        .await;
        assert!(
            activate(&url, "run-token", &state, &attempt, "codex", &mut env)
                .await
                .is_err(),
            "a rolled-back server must not bypass the activation fence"
        );
        let old_server = json!({"subscriptions":[{"id":"auto-login", "runtime":"codex"}]});
        assert!(!pending(&old_server, "codex"));
        assert!(
            available(&given, &old_server, "codex"),
            "old server up-front values stay usable"
        );
        std::fs::remove_dir_all(state).unwrap();
    }

    #[test]
    fn an_antigravity_plan_gets_a_private_home_per_turn_with_the_bots_conversations_and_nothing_else(
    ) {
        use std::os::unix::fs::PermissionsExt;
        let state = dir();
        let own = state.join("bot/.gemini");
        for rel in [
            "antigravity-cli/conversations/c1.pb",
            "settings.json",
            "antigravity-cli/settings.json",
            "GEMINI.md",
        ] {
            std::fs::create_dir_all(own.join(rel).parent().unwrap()).unwrap();
            std::fs::write(own.join(rel), "x").unwrap();
        }
        // The computer's own sign-in, which no plan turn may see.
        std::fs::write(own.join(AGY_TOKEN), "computer-token").unwrap();
        let item = |value: &str| {
            json!([{"id": "agyplan", "runtime": "antigravity", "kind": "antigravity_login", "file": AGY_TOKEN,
                    "value": value, "revision": 1, "billing": "subscription"}])
        };
        let value = r#"{"token":{"access_token":"ya29.synthetic-access-token-0123456789"}}"#;
        let mut turns = vec![];
        for _ in 0..2 {
            let mut env: Env = [
                ("HOME".into(), state.join("bot").display().to_string()),
                ("GOOGLE_API_KEY".into(), "AIza-computer".into()),
            ]
            .into();
            let mut values = vec![];
            let mut got = apply(
                &state,
                &item(value),
                &mut env,
                &mut values,
                &mut BTreeSet::new(),
            );
            assert!(values
                .iter()
                .any(|v| v == "ya29.synthetic-access-token-0123456789"));
            start(&mut got, "antigravity", &mut env).unwrap();
            // The bot's other Google sign-ins stay in the turn's environment for its other runtimes: only agy's own
            // process goes without them, and over SSH (hosts/antigravity.rs).
            assert!(env.contains_key("GOOGLE_API_KEY") && !env.contains_key("SSH_CONNECTION"));
            let home = PathBuf::from(&env["GEMINI_HOME"]);
            assert_eq!(
                plan_home(&got, "antigravity"),
                Some(env["GEMINI_HOME"].clone())
            );
            let t = turn_home("antigravity").unwrap();
            assert_eq!(t.sign_in(&env["GEMINI_HOME"]).unwrap(), value);
            let token = home.join(AGY_TOKEN);
            assert_eq!(std::fs::read_to_string(&token).unwrap(), value);
            assert_eq!(
                token.metadata().unwrap().permissions().mode() & 0o777,
                0o600
            );
            assert!(!std::fs::symlink_metadata(token.parent().unwrap())
                .unwrap()
                .is_symlink());
            assert_eq!(
                std::fs::read_link(home.join("antigravity-cli/conversations")).unwrap(),
                own.join("antigravity-cli/conversations")
            );
            assert_eq!(
                std::fs::read_link(home.join("GEMINI.md")).unwrap(),
                own.join("GEMINI.md")
            );
            assert!(
                !home.join("settings.json").exists()
                    && !home.join("antigravity-cli/settings.json").exists()
            );
            // Two plans, or a plan and the computer, resume one conversation; a shell of another runtime never runs on it.
            assert_eq!(got[0].binding().as_deref(), Some(SHARED_BINDING));
            assert!(!got[0].for_shell());
            turns.push((got, home));
        }
        assert_ne!(turns[0].1, turns[1].1);
        // The first turn's agy refreshed its access token: masked for that turn, never kept or sent back.
        let refreshed = r#"{"token":{"access_token":"ya29.synthetic-refreshed-token-9876543210"}}"#;
        std::fs::write(turns[0].1.join(AGY_TOKEN), refreshed).unwrap();
        for (i, (mut got, home)) in turns.into_iter().enumerate() {
            let masked = finish(&mut got);
            assert_eq!(
                masked
                    .iter()
                    .any(|m| m == "ya29.synthetic-refreshed-token-9876543210"),
                i == 0
            );
            assert!(got[0].refreshed.is_none());
            assert!(!home.exists());
        }
        // A sign-in file that is gone, a link, or no token is no sign-in: agy is never started on it.
        let t = turn_home("antigravity").unwrap();
        let fake = state.join("fake");
        std::fs::create_dir_all(fake.join("antigravity-cli")).unwrap();
        let fake_s = fake.display().to_string();
        assert!(t.sign_in(&fake_s).is_err());
        std::fs::write(fake.join(AGY_TOKEN), r#"{"token":{}}"#).unwrap();
        assert!(t.sign_in(&fake_s).is_err());
        std::fs::remove_file(fake.join(AGY_TOKEN)).unwrap();
        std::os::unix::fs::symlink(own.join(AGY_TOKEN), fake.join(AGY_TOKEN)).unwrap();
        assert!(t.sign_in(&fake_s).is_err());
        assert_eq!(
            std::fs::read_to_string(own.join(AGY_TOKEN)).unwrap(),
            "computer-token"
        );
        assert!(own.join("antigravity-cli/conversations/c1.pb").exists());
        // A file the runner does not know, or a home it cannot make, never runs the turn on another login.
        let mut env: Env = [("HOME".into(), state.join("bot").display().to_string())].into();
        let bad = json!([{"id": "agyplan", "runtime": "antigravity", "file": "../token", "value": value, "revision": 1}]);
        assert!(apply(&state, &bad, &mut env, &mut vec![], &mut BTreeSet::new()).is_empty());
        let mut got = apply(
            &state,
            &item(value),
            &mut env,
            &mut vec![],
            &mut BTreeSet::new(),
        );
        let home = got[0].home.clone().unwrap();
        std::fs::create_dir_all(&home).unwrap();
        std::os::unix::fs::symlink(own.join("antigravity-cli"), home.join("antigravity-cli"))
            .unwrap();
        assert!(start(&mut got, "antigravity", &mut env).is_err());
        assert!(!env.contains_key("GEMINI_HOME"));
        // So for a ChatGPT plan's home: no silent fall back to the computer's own sign-in.
        let codex = json!([{"id": "cxplan", "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                            "value": "{\"tokens\":{\"refresh_token\":\"synthetic-refresh-0123456789\"}}",
                            "revision": 1, "billing": "subscription"}]);
        let mut got = apply(&state, &codex, &mut env, &mut vec![], &mut BTreeSet::new());
        let home = got[0].home.clone().unwrap();
        std::fs::create_dir_all(home.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&own, &home).unwrap();
        assert!(start(&mut got, "codex", &mut env).is_err());
        assert!(!own.join("auth.json").exists());
        // A link anywhere above the home (here the plan's `turns`) is refused before anything is made through it.
        let elsewhere = state.join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        let mut got = apply(
            &state,
            &item(value),
            &mut env,
            &mut vec![],
            &mut BTreeSet::new(),
        );
        let turns_dir = got[0]
            .home
            .clone()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf();
        let _ = std::fs::remove_dir_all(&turns_dir);
        std::os::unix::fs::symlink(&elsewhere, &turns_dir).unwrap();
        assert!(start(&mut got, "antigravity", &mut env).is_err());
        assert!(std::fs::read_dir(&elsewhere).unwrap().next().is_none());
        // A turn's home swapped for a link while it ran: letting go of it removes the link, never what it points at,
        // and reads nothing through it.
        std::fs::remove_file(&turns_dir).unwrap();
        let mut got = apply(
            &state,
            &item(value),
            &mut env,
            &mut vec![],
            &mut BTreeSet::new(),
        );
        start(&mut got, "antigravity", &mut env).unwrap();
        let turn = got[0].home.clone().unwrap().parent().unwrap().to_path_buf();
        let kept = state.join("kept/antigravity/antigravity-cli");
        std::fs::create_dir_all(&kept).unwrap();
        std::fs::write(kept.join("antigravity-oauth-token"), "not the plan's").unwrap();
        std::fs::remove_dir_all(&turn).unwrap();
        std::os::unix::fs::symlink(state.join("kept"), &turn).unwrap();
        assert!(!finish(&mut got).iter().any(|s| s == "not the plan's"));
        assert!(kept.join("antigravity-oauth-token").exists());
        assert!(std::fs::symlink_metadata(&turn).is_err());
        assert_eq!(
            std::fs::read_to_string(own.join(AGY_TOKEN)).unwrap(),
            "computer-token"
        );
        std::fs::remove_dir_all(state).unwrap();
    }

    #[tokio::test]
    async fn a_refresh_the_server_refused_is_never_the_next_turns_login_and_an_unsent_one_is() {
        let state = dir();
        let item = |id: &str, rev: i64| {
            json!([{"id": id, "runtime": "codex", "kind": "codex_login", "file": "auth.json",
                                                "value": "{\"tokens\":1}", "revision": rev}])
        };
        let refreshed = |id: &str| {
            let mut got = turn(&state, &item(id, 1), "codex");
            std::fs::write(got[0].file.as_ref().unwrap(), "{\"tokens\":2}").unwrap();
            finish(&mut got);
            got
        };
        let kept = |id: &str| {
            homes()
                .kept
                .get(
                    &crate::isolation::bot_state(&state)
                        .join("subscriptions")
                        .join(id)
                        .join("codex"),
                )
                .cloned()
        };
        // Refused (another account, or nothing new): dropped, so the next turn starts from what the server holds.
        let got = refreshed("s11");
        let (url, seen) = crate::client::tests::serve(vec![(200, r#"{"stored": false}"#)]).await;
        send_back(
            &crate::client::Client::new(&url, "t", 5, 0).unwrap(),
            "a1",
            &got,
        )
        .await;
        assert!(seen.lock().unwrap()[0].contains("runner-model-subscription-refresh"));
        assert_eq!(kept("s11"), None);
        let mut next = turn(&state, &item("s11", 1), "codex");
        assert_eq!(next[0].written, "{\"tokens\":1}");
        finish(&mut next);
        // Kept: the copy moves to the server's new revision.
        let got = refreshed("s12");
        let (url, _) =
            crate::client::tests::serve(vec![(200, r#"{"stored": true, "revision": 2}"#)]).await;
        send_back(
            &crate::client::Client::new(&url, "t", 5, 0).unwrap(),
            "a2",
            &got,
        )
        .await;
        assert_eq!(kept("s12"), Some((2, "{\"tokens\":2}".to_string())));
        // Unanswered: it stays, the newest login this computer has.
        let got = refreshed("s13");
        let closed = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", closed.local_addr().unwrap());
        drop(closed);
        send_back(
            &crate::client::Client::new(&url, "t", 5, 0).unwrap(),
            "a3",
            &got,
        )
        .await;
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
            for name in [
                "auth.json",
                "auth.tico-0123abcd",
                ".tico-revision",
                "sessions/t.jsonl",
                "config.toml",
            ] {
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
        let login = |n: u8| {
            json!({
            "OPENAI_API_KEY": format!("sk-proj-synthetic-key-{n}-aaaaaaaaaaaa"),
            "tokens": {"id_token": format!("eyJsynthetic.id-token-{n}.sig"), "access_token": format!("eyJsynthetic.access-{n}.sig"),
                       "refresh_token": format!("rt_synthetic_refresh_{n}_bbbbbbbb"), "account_id": "acct"},
            "last_refresh": "2026-10-30T12:00:00.000000Z",
        }).to_string()
        };
        let item = json!([{"id": "s6", "runtime": "codex", "kind": "codex_login", "file": "auth.json", "value": login(1), "revision": 1}]);
        let mut values = vec![];
        let mut got = apply(
            &state,
            &item,
            &mut Env::new(),
            &mut values,
            &mut BTreeSet::new(),
        );
        values.extend(start(&mut got, "codex", &mut Env::new()).unwrap());
        let r = crate::redact::Redactor::new(&values);
        for leaked in [
            "sk-proj-synthetic-key-1-aaaaaaaaaaaa",
            "eyJsynthetic.id-token-1.sig",
            "eyJsynthetic.access-1.sig",
            "rt_synthetic_refresh_1_bbbbbbbb",
        ] {
            let out = r
                .scrub_text(&format!("echo {leaked} > notes.txt"))
                .into_owned();
            assert!(!out.contains(leaked), "{out}");
        }
        // Not a secret: the refresh time stays readable.
        assert!(r
            .scrub_text("at 2026-10-30T12:00:00.000000Z")
            .contains("2026-10-30T12"));
        // Codex refreshed it: the new tokens come back from `finish` for the turn's redactor, before anything is sent.
        std::fs::write(got[0].file.as_ref().unwrap(), login(2)).unwrap();
        let fresh = finish(&mut got);
        r.add(&fresh);
        for leaked in [
            "eyJsynthetic.access-2.sig",
            "rt_synthetic_refresh_2_bbbbbbbb",
            "eyJsynthetic.id-token-2.sig",
        ] {
            assert!(!r.scrub_text(leaked).contains(leaked), "{leaked}");
        }
        // A file kept from that refresh is masked at the next turn too, from its start, as well as what the server handed.
        let mut values = vec![];
        let mut next = apply(
            &state,
            &item,
            &mut Env::new(),
            &mut values,
            &mut BTreeSet::new(),
        );
        let r = crate::redact::Redactor::new(&values);
        for leaked in [
            "rt_synthetic_refresh_1_bbbbbbbb",
            "rt_synthetic_refresh_2_bbbbbbbb",
        ] {
            assert!(!r.scrub_text(leaked).contains(leaked), "{leaked}");
        }
        start(&mut next, "codex", &mut Env::new()).unwrap();
        assert_eq!(next[0].written, login(2));
        finish(&mut next);
        std::fs::remove_dir_all(&state).unwrap();
    }
}
