//! The runtime hosts: one runtime behind start/thread/turn/steer/interrupt calls and
//! one event channel. Events are the Python host's dicts, as JSON, so the outbox rows match it field for field:
//!
//! delta {text}, message {text, final}, turn_completed {status}, turn_failed {error, limit, auth_retry},
//! status {state}, tokens {input, output, total, usage{input, cached, output}}, rate_limits, error, diagnostic.

pub mod antigravity;
pub mod appdriver;
pub mod claude;
pub mod codex;
pub mod cursor;
pub mod gemini;
pub mod grok;
mod hub_paths;
#[cfg(test)]
mod isolationtests;
pub mod musecode;
mod native;
mod oneshot;
pub mod pi;
#[cfg(test)]
mod replay;

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};
use tokio::sync::mpsc::UnboundedSender;

#[cfg(test)]
thread_local! {
    pub(crate) static TEST_STARTS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

use crate::env::Env;
use hub_paths::{HUB_PATHS, HUB_READ_PATHS};

pub type Events = UnboundedSender<Value>;

/// A host may prepare threads without a process. Only a successful spawn or reuse of a live process
/// crosses memory admission; the turn keeps that boundary through host shutdown and fallback.
#[derive(Default)]
pub struct Allocation(std::sync::Mutex<AllocationState>);

#[derive(Default)]
struct AllocationState {
    allocated: bool,
    turn: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
}

impl Allocation {
    fn track(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        let mut state = self.0.lock().unwrap();
        if state.allocated {
            turn.store(true, std::sync::atomic::Ordering::SeqCst);
        }
        state.turn = Some(turn);
    }

    fn mark(&self) {
        let mut state = self.0.lock().unwrap();
        state.allocated = true;
        if let Some(turn) = &state.turn {
            turn.store(true, std::sync::atomic::Ordering::SeqCst);
        }
    }
}

#[derive(Debug)]
pub enum HostError {
    /// The runtime refused a call, or died while answering one.
    Failed(String),
    /// `steer` wrote nothing because the turn is over or ending: the runner gives the input a turn of its own.
    SteerRefused(String),
}

impl std::fmt::Display for HostError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HostError::Failed(m) | HostError::SteerRefused(m) => f.write_str(m),
        }
    }
}

pub fn failed(text: impl Into<String>) -> HostError {
    HostError::Failed(text.into())
}

/// One rule for a usage limit, so every host classifies it the same way (base.py LIMIT_RE).
pub fn limit_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)hit your (?:usage|session|weekly) limit|usage limit|usage_limit_reached|subscription quota exhausted|quota exceeded\. check your plan|exceeded your current quota|insufficient[_ ]quota|rate[ _]limit(?:ed|_error)?|spend(?:ing)? limit|monthly (?:usage |spend(?:ing)? )?limit|no capacity available|model is at capacity|\boverloaded(?:_error)?\b|resource_exhausted|\bout_of_credit\b|\b(?:code|status|http) (?:429|503)\b",
        )
        .unwrap()
    })
}

/// A provider out of capacity for the moment, not a login's quota (backend/login_holds.py _CAPACITY_RE, _QUOTA_RE):
/// still a limit, so a fallback can take the turn, but the server holds the login for minutes, not a quota backoff.
fn capacity_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)model is at capacity|no capacity available|\boverloaded(?:_error)?\b|\b(?:code|status|http) 503\b|(?:retry|try again) (?:after|in) \d+(?:\.\d+)? ?(?:s|secs?|seconds)\b").unwrap()
    })
}

fn quota_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)hit your (?:usage|session|weekly) limit|usage limit|usage_limit_reached|quota|spend(?:ing)? limit|monthly (?:usage |spend(?:ing)? )?limit|out_of_credit|billing|\bresets?\b").unwrap()
    })
}

fn auth_retry_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)failed to refresh oauth token|another claude code process is refreshing|exited mid-refresh|oauth_refresh_lock|lock_busy|lock_timeout|unexpected status 401 unauthorized",
        )
        .unwrap()
    })
}

fn auth_rejected_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)incorrect api key|invalid[ _-]?(?:x-)?api[ _-]?key|api key (?:is )?(?:invalid|revoked|expired)|not logged in|please run /login|invalid authentication credentials|authentication_error|oauth session expired and could not be refreshed|missing meta credentials|missing bearer or basic authentication",
        )
        .unwrap()
    })
}

fn secretish_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\b(?:sk|pk|xai|gsk|key|ghp|github_pat)[-_][A-Za-z0-9_-]{6,}|Bearer\s+\S+|[A-Za-z0-9_-]{32,}").unwrap())
}

pub fn is_limit(text: &str) -> bool {
    limit_re().is_match(text)
}

/// Whether a limit notice is the provider's temporary model capacity: quota or billing words in it win.
pub fn is_capacity(text: &str) -> bool {
    capacity_re().is_match(text) && !quota_re().is_match(text)
}

pub fn is_auth_rejected(text: &str) -> bool {
    auth_rejected_re().is_match(text)
}

pub fn is_auth_retryable(text: &str) -> bool {
    auth_retry_re().is_match(text) && !is_auth_rejected(text)
}

/// One line of the provider's refusal for people to read; keys and tokens are never kept.
pub fn rejection_reason(text: &str) -> String {
    let line = text
        .lines()
        .map(str::trim)
        .find(|row| is_auth_rejected(row))
        .map(String::from)
        .unwrap_or_default();
    let line = if line.is_empty() {
        text.trim().to_string()
    } else {
        line
    };
    secretish_re()
        .replace_all(&line, "[redacted]")
        .chars()
        .take(200)
        .collect()
}

pub const DEFAULT_COMPACT_AT: i64 = 250_000;

/// A bot's "Compact at" in tokens: its own whole number, else DEFAULT_COMPACT_AT.
pub fn compact_at(value: &Value) -> i64 {
    compact_at_set(value).unwrap_or(DEFAULT_COMPACT_AT)
}

/// The "Compact at" the turn was given, in tokens; None when it carries none (a harness then keeps its own default).
pub fn compact_at_set(value: &Value) -> Option<i64> {
    let n = match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    };
    n.filter(|t| *t > 0)
}

#[derive(Clone, Debug, Default)]
pub struct Settings {
    /// Only a Gateway harness receives its turn's leased OS identity.
    pub identity: Option<(u32, u32)>,
    pub cwd: String,
    pub model: Option<String>,
    pub effort: Option<String>,
    pub env: Env,
    #[allow(dead_code)] // the hub MCP server and Codex read the bot from the environment
    pub slug: String,
    pub compact_at: Value,
    /// What each Gateway model of the turn reads, `{model id: ["text", "image", "pdf"]}` from the server's
    /// `gateway_inputs`; Null from an older server (hosts/pi.rs `gateway_models_json_for`).
    pub gateway_inputs: Value,
    /// The task's worktrees in other repos the session may work in (Claude `--add-dir`; others ignore it).
    pub add_dirs: Vec<String>,
    /// The turn's staged company skills folder (crate::company_skills::stage): Codex loads its skills as the app
    /// server's extra skill root. Claude reads the same folder through `add_dirs`.
    pub company_skills: Option<String>,
    /// The bot's own MCP servers from bot.yaml, this harness's transports only (crate::perbot::mcp_for_run).
    pub mcp_servers: Vec<Value>,
    /// How long a Claude turn that has answered may wait on nothing but its background shells before it ends and
    /// leaves them running (`background_wait_s`); None waits for them, as before.
    pub background_wait: Option<std::time::Duration>,
    /// A capacity continuation must fail if its saved session is missing; rebuilding context may repeat work.
    pub require_existing_session: bool,
    /// Where Claude Code keeps its auto memory for this turn: `memory/claude/` in the turn's checkout of the bot's
    /// repository, so what it remembers is committed with the rest of memory/ and follows the bot to any computer.
    /// None keeps auto memory off (a stateless bot, a private meeting's copy, a turn outside a checkout).
    pub memory_dir: Option<String>,
}

/// `background_wait_s` in the bot's run config: seconds, 0 for off; five minutes when unset.
pub const BACKGROUND_WAIT_S: f64 = 300.0;

pub fn background_wait(config: &Value) -> Option<std::time::Duration> {
    let v = &config["background_wait_s"];
    let s = v
        .as_f64()
        .or_else(|| v.as_str().and_then(|t| t.trim().parse().ok()))
        .unwrap_or(BACKGROUND_WAIT_S);
    (s > 0.0 && s.is_finite()).then(|| std::time::Duration::from_secs_f64(s))
}

const HUB_MCP_ENV: &[&str] = &[
    "HUB_API_URL",
    "HUB_TOKEN",
    "HUB_BOT",
    "HUB_EMPLOYEE",
    "HUB_DIR",
];

/// The hub as a stdio MCP server for one turn (`hub_mcp_server`), or None outside a turn.
pub fn hub_mcp_server(env: &Env) -> Option<Value> {
    if env.get("HUB_API_URL").is_none_or(|v| v.is_empty())
        || env.get("HUB_TOKEN").is_none_or(|v| v.is_empty())
    {
        return None;
    }
    let mut vars = Map::new();
    for k in HUB_MCP_ENV {
        if let Some(v) = env.get(*k).filter(|v| !v.is_empty()) {
            vars.insert(k.to_string(), json!(v));
        }
    }
    let script = crate::env::root().join("clients").join("hubmcp.py");
    Some(
        json!({"command": crate::env::python().display().to_string(), "args": [script.display().to_string()], "env": vars}),
    )
}

/// An event dict: `kind`, `thread_id`, `turn_id`, then its own fields.
pub fn event(kind: &str, thread: Option<&str>, turn: Option<&str>, fields: Value) -> Value {
    let mut ev = Map::new();
    ev.insert("kind".into(), json!(kind));
    ev.insert("thread_id".into(), thread.map_or(Value::Null, |t| json!(t)));
    ev.insert("turn_id".into(), turn.map_or(Value::Null, |t| json!(t)));
    if let Value::Object(f) = fields {
        ev.extend(f);
    }
    Value::Object(ev)
}

/// Adds one streamed piece to a turn's reply and returns the text its `delta` carries. Pieces of one message join
/// with nothing between them; the first piece of a new message after earlier text starts a paragraph of its own,
/// as Claude's separate answers do, so two messages never read as one ("...recovers.No change...").
pub fn add_piece(reply: &mut String, new_message: bool, text: &str) -> String {
    if !new_message || reply.trim().is_empty() {
        reply.push_str(text);
        return text.to_string();
    }
    reply.truncate(reply.trim_end().len());
    let piece = format!("\n\n{}", text.trim_start());
    reply.push_str(&piece);
    piece
}

/// The longest input summary a `tool` event carries. The drive loop scrubs it with the rest of the event.
pub const TOOL_SUMMARY_CHARS: usize = 120;

/// What a tool call acts on, without its arguments: the file it names, or the program its command runs. Run logs
/// show this to everyone who can see the run, and arguments can carry tokens or private text.
pub fn tool_summary(input: &Value) -> String {
    let text = |k: &str| {
        input
            .get(k)
            .and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty())
    };
    if let Some(path) = ["file_path", "notebook_path", "path"]
        .iter()
        .find_map(|k| text(k))
    {
        return clip(path);
    }
    text("command").map(command_program).unwrap_or_default()
}

/// Only fixed operation labels leave the computer. Commands can contain private text and credentials,
/// while a program-only display summary cannot distinguish `gh pr view` from `gh pr merge`.
pub fn shell_outside_actions(command: &str) -> Vec<&'static str> {
    static RULES: OnceLock<Vec<(&'static str, Regex)>> = OnceLock::new();
    let rules =
        RULES.get_or_init(|| {
            [
            (
                "merge pull request",
                r"\bgh\s+(?:pr\s+merge\b|api\b[^\n]*\bmerg(?:e|ed|ing)\b)",
            ),
            (
                "publish release",
                r"\bgh\s+release\s+create\b|\b(?:hub|tico)\s+release\s+now\b",
            ),
            (
                "publish package",
                r"\b(?:npm|cargo)\s+publish\b|\btwine\s+upload\b|\bdocker\s+(?:image\s+)?push\b",
            ),
            ("consume approval", r"\b(?:(?:hub|tico)\s+)?approval\s+consume\b"),
            (
                "send email",
                r"\b(?:(?:hub|tico)\s+)?(?:mail|email)\s+send\b|\bsendmail\b",
            ),
            (
                "send webhook",
                r"\b(?:(?:hub|tico)\s+)?webhook\s+(?:send|retry|recover)\b",
            ),
            ("send support report", r"\b(?:hub|tico)\s+support\s+send\b"),
            (
                "make payment",
                r"\bstripe\s+(?:payments|payment_intents)\s+(?:create|confirm)\b",
            ),
        ]
        .into_iter()
        .map(|(label, pattern)| (label, Regex::new(&format!("(?i){pattern}")).unwrap()))
        .collect()
        });
    // Normalize shell quoting as well as scanning the raw command: quoted subcommands and nested shell
    // wrappers must retain their action. Matching mentions can conservatively hold; it never exports them.
    let normalized = shell_words(command)
        .map(|words| words.join(" "))
        .unwrap_or_default();
    rules
        .iter()
        .filter(|(_, pattern)| pattern.is_match(command) || pattern.is_match(&normalized))
        .map(|(label, _)| *label)
        .collect()
}

/// Whether a shell line only reads, judged on the whole line: every run of it (`|`, `;`, `&&`, `||`, newlines) is setup
/// (`cd <dir>`, `export NAME=value`, `env NAME=value ...`, bare `NAME=value`), a program from READ_ONLY_PROGRAMS, or
/// `hub` with a command path from HUB_READ_PATHS. Output may go only to `/dev/null` or another descriptor (`2>&1`), and
/// input only from a plain file. False for anything it cannot read whole: a heredoc, `$` or backtick expansion, a
/// subshell, a background `&`, `xargs`, `eval`, `source`, any other program, or a shell wrapper whose script is not
/// plainly one word (`bash -lc '...'` is read as its script, as Codex sends every command). The backend requeues a turn
/// whose lease lapsed only when every shell call it ran says true here (backend/execution.py read_only_shells).
pub fn shell_reads_only(command: &str) -> bool {
    if let Some(script) = shell_script(command) {
        return shell_reads_only(&script);
    }
    if command.contains("<<") || outer_expands(command) {
        return false;
    }
    let runs: Vec<&str> = command_runs(command)
        .into_iter()
        .filter(|run| !run.trim().is_empty())
        .collect();
    !runs.is_empty() && runs.into_iter().all(run_reads_only)
}

/// Programs that only read files or print, with no flag that runs another program or writes a file
/// (the few that have one are refused in `run_reads_only`).
const READ_ONLY_PROGRAMS: &[&str] = &[
    "ls", "pwd", "cat", "head", "tail", "grep", "rg", "wc", "jq", "stat", "which", "date", "sort",
    "uniq", "diff", "tree", "du", "df", "echo", "printf",
];

/// Whether one run of a shell line only reads (`shell_reads_only`).
fn run_reads_only(run: &str) -> bool {
    static REDIRECT: OnceLock<Regex> = OnceLock::new();
    let redirect = REDIRECT.get_or_init(|| {
        Regex::new(concat!(
            r"(?:(?:&|[0-9]*)>>?[ \t]*/dev/null|[0-9]*>&(?:[0-9]+|-)|[0-9]*<[ \t]*[A-Za-z0-9._/~+-]+)",
            r"(?:[ \t]|$)"
        ))
        .unwrap()
    });
    // What the shell itself reads, with quoted and escaped text blanked out: past the allowed redirections, no
    // redirection, background `&`, subshell or group may be left.
    let Some(bare) = unquoted(run) else {
        return false;
    };
    if redirect
        .replace_all(&bare, " ")
        .contains(['&', '>', '<', '(', ')', '{', '}'])
    {
        return false;
    }
    let Some(words) = shell_words(run) else {
        return false;
    };
    let words: Vec<String> = words.into_iter().skip_while(|w| is_assignment(w)).collect();
    words_read_only(&words)
}

/// Whether a run's words (past its leading assignments) only set up or read.
fn words_read_only(words: &[String]) -> bool {
    let Some((first, args)) = words.split_first() else {
        return true;
    };
    let short = |letters: &str| {
        args.iter().any(|a| {
            a.starts_with('-') && !a.starts_with("--") && a.contains(|c| letters.contains(c))
        })
    };
    let long = |names: &[&str]| args.iter().any(|a| names.iter().any(|n| a.starts_with(n)));
    let globbed = args.iter().any(|a| a.contains(['*', '?', '[']));
    match first.as_str() {
        "cd" => args.len() <= 1,
        "export" => !args.is_empty() && args.iter().all(|w| is_assignment(w)),
        "env" => {
            let rest: Vec<String> = args
                .iter()
                .skip_while(|w| is_assignment(w) || *w == "-i" || *w == "-0")
                .cloned()
                .collect();
            rest.first().is_none_or(|w| !w.starts_with('-')) && words_read_only(&rest)
        }
        // `tico` is the teamwork CLI; `hub` is its old name, still read.
        "hub" | "tico" => hub_summary(words)
            .split_once(' ')
            .is_some_and(|(_, path)| HUB_READ_PATHS.contains(&path)),
        // A file name that expands to a flag could turn on one of the flags refused below.
        "sort" | "tree" | "uniq" | "date" | "rg" if globbed => false,
        "sort" => !short("o") && !long(&["--output", "--compress"]),
        "tree" => !short("oR") && !long(&["--output"]),
        // `uniq IN OUT` writes OUT: only flags (reading standard input) are taken.
        "uniq" => args.iter().all(|a| a.starts_with('-')),
        "date" => !short("s") && !long(&["--set"]),
        "rg" => !long(&["--pre", "--hostname-bin"]),
        program => READ_ONLY_PROGRAMS.contains(&program),
    }
}

/// A shell run as the shell reads it, with the text of every quote and escape replaced by `x`; None when a quote is left
/// open.
fn unquoted(run: &str) -> Option<String> {
    let (mut out, mut quote) = (String::with_capacity(run.len()), None::<char>);
    let mut chars = run.chars();
    while let Some(c) = chars.next() {
        match (quote, c) {
            (Some(q), _) if c == q => {
                quote = None;
                out.push('x');
            }
            (Some('"'), '\\') => {
                chars.next();
                out.push_str("xx");
            }
            (Some(_), _) => out.push('x'),
            (None, '\'' | '"') => {
                quote = Some(c);
                out.push('x');
            }
            (None, '\\') => {
                chars.next();
                out.push_str("xx");
            }
            _ => out.push(c),
        }
    }
    quote.is_none().then_some(out)
}

/// What a shell command does, for a tool event: `hub` and its command path when a run of the line plainly starts with
/// `hub`, else the program the whole line plainly starts with. Both are read from the raw text, never from words a shell
/// would rebuild: a program counts only when it is the first thing in its text, unquoted, after nothing but plain
/// `NAME=value` assignments (`plain_program`). Anything else (a leading redirection, quoting, an expansion, a subshell)
/// gives nothing, and only the line before a heredoc is read, so its body never names a step.
pub fn command_program(command: &str) -> String {
    // A `sh -c` wrapper (Codex runs every command as `/bin/bash -lc '...'`) is read as the script it runs.
    if let Some(script) = shell_script(command) {
        return command_program(&script);
    }
    let command = if command.contains("<<") {
        command.split('\n').next().unwrap_or("")
    } else {
        command
    };
    // A blank run (a comment's end, a trailing separator) holds no work.
    let runs: Vec<&str> = command_runs(command)
        .into_iter()
        .filter(|run| !run.trim().is_empty())
        .collect();
    if let Some(words) = runs.iter().find_map(|run| hub_words(run)) {
        return hub_summary(&words);
    }
    // The work is the first run past the setup runs before it: `cd web && npm test` names npm. A line that is only
    // setup names its last run, so `cd /tmp` still reads `cd`.
    let mut work = runs.as_slice();
    while let [setup, rest @ ..] = work {
        if rest.is_empty() || !is_setup_run(setup) {
            break;
        }
        work = rest;
    }
    work.first()
        .and_then(|run| plain_program(strip_env(run)))
        .map(clip)
        .unwrap_or_default()
}

/// A run that only sets the next one up and does no work of its own: `cd <dir>`, `source <file>`, `. <file>`,
/// `export NAME=value`, `env NAME=value` or bare `NAME=value` assignments. Its words are read only to tell its shape,
/// never shown, and a redirection makes it work, not setup.
fn is_setup_run(run: &str) -> bool {
    let Some(words) = shell_words(run) else {
        return false;
    };
    let Some((first, args)) = words.split_first() else {
        return true;
    };
    match first.as_str() {
        "cd" | "source" | "." => {
            args.len() <= 1 && args.iter().all(|w| !w.contains(['<', '>', '|', '&', ';']))
        }
        "export" => !args.is_empty() && args.iter().all(|w| is_assignment(w)),
        "env" => args
            .iter()
            .all(|w| is_assignment(w) || w == "-i" || w == "-0"),
        _ => is_assignment(first) && args.iter().all(|w| is_assignment(w)),
    }
}

/// A run without its leading `env` and the `NAME=value` words (or `-i`, `-0`) after it, so `env TOKEN=x tico task list`
/// reads as `tico task list`. Only those flags are taken: another flag may take a word of its own, which is never shown
/// in its place. A run that is only `env` and its words is kept whole.
fn strip_env(run: &str) -> &str {
    static ENV: OnceLock<Regex> = OnceLock::new();
    let env = ENV.get_or_init(|| {
        Regex::new(
            r#"^[ \t]*env(?:[ \t]+(?:-[i0]|[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"\\$`]*"|[A-Za-z0-9._/:@%+,-])*))*[ \t]+"#,
        )
        .unwrap()
    });
    match env.find(run) {
        Some(m) if !run[m.end()..].trim().is_empty() && !run[m.end()..].starts_with('-') => {
            &run[m.end()..]
        }
        _ => run,
    }
}

/// The script of a line that is only `sh`, `bash`, `zsh` or `dash`, one plain `-c` flag (`-c`, `-lc`, `-ec`) and one script word.
fn shell_script(command: &str) -> Option<String> {
    let words = shell_words(command)?;
    let [shell, flag, script] = words.as_slice() else {
        return None;
    };
    // The line itself reads `<shell> <flag> ` as written, unquoted, so no quoting, separator or redirection changes them.
    let blank = [' ', '\t'];
    let plain = shell
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "._+/-".contains(c))
        && command
            .trim_start_matches(blank)
            .strip_prefix(shell.as_str())
            .filter(|rest| rest.starts_with(blank))
            .and_then(|rest| rest.trim_start_matches(blank).strip_prefix(flag.as_str()))
            .is_some_and(|rest| rest.starts_with(blank));
    let name = shell.rsplit('/').next().unwrap_or(shell);
    // Only flag groups whose every option takes no argument: `-o` would take the next word, and `--norc` is a long
    // option whose next word is a file.
    // A `$` or backtick the outer shell sees expands before the inner shell runs, so the rebuilt script is not
    // what runs: leave such a line labelled by its shell.
    (plain
        && !outer_expands(command)
        && matches!(name, "sh" | "bash" | "zsh" | "dash")
        && matches!(
            flag.as_str(),
            "-c" | "-lc" | "-ic" | "-ec" | "-xc" | "-lic" | "-ilc"
        ))
    .then(|| script.clone())
}

/// Whether the line has a `$` or backtick outside single quotes, where the shell running it would expand it.
fn outer_expands(command: &str) -> bool {
    let (mut single, mut double) = (false, false);
    let mut chars = command.chars();
    while let Some(c) = chars.next() {
        match c {
            '\'' if !double => single = !single,
            _ if single => {}
            '\\' => {
                chars.next();
            }
            '"' => double = !double,
            '$' | '`' => return true,
            _ => {}
        }
    }
    false
}

/// The program a text plainly starts with: its first word after plain or plainly quoted assignments, when that word is a plain name or
/// path (`[A-Za-z0-9._+/-]`) ending at whitespace, a separator or the end. The result is its file name, checked by
/// `program_name_ok`; None for any other text.
fn plain_program(text: &str) -> Option<&str> {
    static START: OnceLock<Regex> = OnceLock::new();
    let start = START.get_or_init(|| {
        // Only a space or a tab separates words (a shell keeps `\r` or a no-break space inside one), and a newline
        // ends the command. An assignment's value is plain characters, '...' (anything but a quote) or "..." (no `$`, backtick or
        // backslash), so where it ends is exact and nothing in it can run.
        Regex::new(concat!(
            r#"^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"\\$`]*"|[A-Za-z0-9._/:@%+,-])*[ \t]+)*"#,
            r"([A-Za-z0-9._+/-]+)(?:[ \t\n;|&]|$)"
        ))
        .unwrap()
    });
    let path = start.captures(text)?.get(1)?.as_str();
    let program = path.rsplit('/').next().unwrap_or(path);
    program_name_ok(program).then_some(program)
}

/// A run's words from its `hub` word on, when the run plainly starts with `hub` (`plain_program`). The line is cut into
/// runs only for this, and only the command path is read from the words (`hub_summary`), so a mis-split can at worst
/// name a real command path and an argument never reaches the summary.
fn hub_words(run: &str) -> Option<Vec<String>> {
    let run = strip_env(run);
    if !matches!(plain_program(run)?, "hub" | "tico") {
        return None;
    }
    let words = shell_words(run)?;
    let at = words.iter().position(|w| !is_assignment(w))?;
    Some(words[at..].to_vec())
}

/// A program name a summary may show: a plain file name (`npm`, `node18.x`, `g++`), never a word with an expansion or a
/// secret in it.
fn program_name_ok(program: &str) -> bool {
    static NAME: OnceLock<Regex> = OnceLock::new();
    NAME.get_or_init(|| Regex::new(r"^[A-Za-z0-9._+-]{1,40}$").unwrap())
        .is_match(program)
}

/// The runs a shell line holds: its text cut at `&&`, `||`, `|`, `;` and newlines. A separator inside quotes, inside
/// `$(...)` or inside backticks keeps the run whole, and a lone `&` (as in `2>&1`) is not a separator. A `#` that starts
/// a word comments out the rest of its line: the comment is in no run, and a separator inside it splits nothing.
fn command_runs(command: &str) -> Vec<&str> {
    let mut runs: Vec<&str> = vec![];
    let (mut start, mut quote, mut escaped, mut depth, mut ticked) =
        (0, None::<char>, false, 0usize, false);
    let mut chars = command.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if std::mem::take(&mut escaped) {
            continue;
        }
        let doubled = matches!(c, '&' | '|') && matches!(chars.peek(), Some(&(_, n)) if n == c);
        let cut = depth == 0 && !ticked;
        match (quote, c) {
            (Some(q), _) if c == q => quote = None,
            (Some('\''), _) => {}
            (_, '\\') => escaped = true,
            (None, '\'' | '"') => quote = Some(c),
            (None, '(') if command[..i].ends_with('$') => depth += 1,
            (None, ')') if depth > 0 => depth -= 1,
            (None, '`') => ticked = !ticked,
            (None, '#') if starts_word(command, i) => {
                // A comment runs to the end of its line: no run holds its text, and a separator inside it splits nothing.
                let end = command[i..].find('\n').map_or(command.len(), |n| i + n);
                if cut {
                    runs.push(&command[start..i]);
                    start = end;
                }
                while chars.peek().is_some_and(|&(j, _)| j < end) {
                    chars.next();
                }
            }
            (None, '&' | '|') if doubled && cut => {
                chars.next();
                runs.push(&command[start..i]);
                start = i + 2;
            }
            (None, '|' | ';' | '\n') if cut => {
                runs.push(&command[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    runs.push(&command[start..]);
    runs
}

/// Whether the character at byte `i` starts a shell word: nothing, whitespace or an operator comes before it.
fn starts_word(command: &str, i: usize) -> bool {
    command[..i]
        .chars()
        .next_back()
        .is_none_or(|p| p.is_whitespace() || ";|&()".contains(p))
}

/// `hub` and the command path its words name, read in order from the positional words before the first flag: the first
/// word only if it is a top-level command, the next only if it is a subcommand of that command, and so on to three words.
/// Reading stops at the first word that does not continue a path, so an argument, a recipient or a note never reaches the
/// summary (`tico message send finance "x"` reads `tico message send`; `tico task close 7f3a` reads `tico task close`).
fn hub_summary(words: &[String]) -> String {
    let mut path: Vec<&str> = vec![];
    for word in words[1..]
        .iter()
        .take(3)
        .take_while(|w| !w.starts_with('-'))
    {
        path.push(word.as_str());
        if !HUB_PATHS.contains(&path.join(" ").as_str()) {
            path.pop();
            break;
        }
    }
    // The word the line used (`tico`, or its old name `hub`), without a folder (`/usr/local/bin/hub` reads `hub`).
    let program = words[0].rsplit('/').next().unwrap_or("tico");
    std::iter::once(program)
        .chain(path)
        .collect::<Vec<_>>()
        .join(" ")
}

/// A command's words after shell quoting (single, double, backslash), or None when a quote is left open.
fn shell_words(command: &str) -> Option<Vec<String>> {
    let (mut words, mut word, mut started) = (vec![], String::new(), false);
    let mut chars = command.chars();
    while let Some(c) = chars.next() {
        match c {
            // Only the shell's own blanks split words; a carriage return or a no-break space is part of a word.
            ' ' | '\t' | '\n' => {
                if started {
                    words.push(std::mem::take(&mut word));
                    started = false;
                }
            }
            '\'' => {
                started = true;
                loop {
                    match chars.next()? {
                        '\'' => break,
                        c => word.push(c),
                    }
                }
            }
            '"' => {
                started = true;
                loop {
                    match chars.next()? {
                        '"' => break,
                        // Inside double quotes a backslash only escapes $ ` " \ and a
                        // newline; before anything else it stays, as the shell keeps it.
                        '\\' => match chars.next()? {
                            '\n' => {}
                            c @ ('$' | '`' | '"' | '\\') => word.push(c),
                            c => word.extend(['\\', c]),
                        },
                        c => word.push(c),
                    }
                }
            }
            '\\' => match chars.next()? {
                '\n' => {}
                c => {
                    started = true;
                    word.push(c);
                }
            },
            c => {
                started = true;
                word.push(c);
            }
        }
    }
    if started {
        words.push(word);
    }
    Some(words)
}

fn is_assignment(word: &str) -> bool {
    word.split_once('=').is_some_and(|(name, _)| {
        !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
    })
}

fn clip(text: &str) -> String {
    let line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= TOOL_SUMMARY_CHARS {
        line
    } else {
        format!(
            "{}…",
            line.chars()
                .take(TOOL_SUMMARY_CHARS - 1)
                .collect::<String>()
        )
    }
}

/// A way to stop a child process that only one task owns and reaps (`own`). Signals go through that task, so
/// neither SIGTERM nor the later SIGKILL can reach a pid the system has already handed to another process.
#[derive(Clone)]
pub struct Stopper(tokio::sync::mpsc::UnboundedSender<Stop>);

enum Stop {
    Term(std::time::Duration),
    Kill,
    Signal(i32),
}

impl Stopper {
    /// SIGTERM now, SIGKILL after `grace` if the process has not exited by then. A process already reaped is
    /// left alone.
    pub fn terminate(&self, grace: std::time::Duration) {
        let _ = self.0.send(Stop::Term(grace));
    }

    /// SIGKILL now, so the process runs none of its own cleanup (Claude's ends its background shells).
    pub fn kill(&self) {
        let _ = self.0.send(Stop::Kill);
    }

    /// `signal` now, through the task that reaps the process, so never to a pid already handed on.
    pub fn signal(&self, signal: i32) {
        let _ = self.0.send(Stop::Signal(signal));
    }
}

/// A harness process: `program` as `identity` (or the bot user when isolation is on), inside a limited scope of its
/// own where this runner limits turns (crate::turn_limits), else exactly as `isolation::command_as`.
pub fn harness_command(
    identity: Option<(u32, u32)>,
    program: impl AsRef<std::ffi::OsStr>,
) -> tokio::process::Command {
    match crate::turn_limits::prefix() {
        Some(parent) => crate::isolation::command_after(&parent, identity, program),
        None => crate::isolation::command_as(identity, program),
    }
}

/// `harness_command` for a harness that runs as this runner's own user even when isolation is on.
pub fn own_user_harness_command(program: impl AsRef<std::ffi::OsStr>) -> tokio::process::Command {
    match crate::turn_limits::prefix() {
        Some(parent) => {
            let mut cmd = tokio::process::Command::new(&parent[0]);
            cmd.args(&parent[1..]).arg(program);
            cmd
        }
        None => tokio::process::Command::new(program),
    }
}

/// Makes the process `command` starts the leader of a new session and process group, so a stop (`own`) reaches
/// what it starts (tool commands, MCP servers, a browser) as well as itself. A turn's standard streams are piped, so
/// losing the controlling terminal costs nothing, and nothing it runs can stop on a read from one.
pub fn new_group(command: &mut tokio::process::Command) -> &mut tokio::process::Command {
    // SAFETY: setsid is async-signal-safe, and a just-forked child leads no group, so it succeeds.
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    command
}

/// Whether child `pid` has exited, without reaping it: while it stays unreaped its pid, and so the number of the
/// group it leads, can go to no other process. An error (it was reaped already) counts as exited.
fn exited_unreaped(pid: i32) -> bool {
    // SAFETY: with WNOWAIT, waitid only writes the child's state into `info`.
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let r = unsafe {
        libc::waitid(
            libc::P_PID,
            pid as libc::id_t,
            &mut info,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    r != 0 || unsafe { info.si_pid() } != 0
}

/// Hand `child` to a task of its own that waits for it and carries out `Stopper` requests. The receiver gets the
/// exit status. Take the child's pipes before calling this.
///
/// A child that leads its own process group (`new_group`) is stopped as a group: SIGTERM, the SIGKILL after the
/// grace, and, when it exits after a SIGTERM, a SIGKILL to whatever of the group is still left, so nothing it started
/// outlives the stop. That last SIGKILL goes before the child is reaped (so the group's number is still its own) and
/// before its status is sent (so it has gone when a stop returns). What it started had the SIGTERM with it, and Claude
/// ends its own tools on one. `kill` and `signal` reach the child alone, and after either the group is left to its
/// caller: a turn that ends on its background wait keeps shells in that group.
pub fn own(
    mut child: tokio::process::Child,
) -> (
    Stopper,
    tokio::sync::oneshot::Receiver<std::io::Result<std::process::ExitStatus>>,
) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Stop>();
    let (done, exited) = tokio::sync::oneshot::channel();
    let pid = child.id().map(|p| p as i32);
    // SAFETY: getpgid only reads; the child is not reaped yet, so the pid is still its own.
    let mut group = pid.filter(|&p| unsafe { libc::getpgid(p) } == p);
    tokio::spawn(async move {
        // A group leader's exit is watched on each SIGCHLD without reaping it (`exited_unreaped`); any other child is
        // reaped by its wait directly. Without the signal stream, a group leader is too, and nothing follows its exit.
        let mut exits = group.and_then(|_| {
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::child()).ok()
        });
        let watching = exits.is_some();
        let mut kill_at: Option<tokio::time::Instant> = None;
        let mut asking = true;
        let mut termed = false;
        let status = loop {
            let escalate = async {
                match kill_at {
                    Some(at) => tokio::time::sleep_until(at).await,
                    None => std::future::pending().await,
                }
            };
            let leader_exited = async {
                match (exits.as_mut(), pid) {
                    (Some(exits), Some(pid)) => {
                        while !exited_unreaped(pid) {
                            if exits.recv().await.is_none() {
                                std::future::pending::<()>().await;
                            }
                        }
                    }
                    _ => std::future::pending().await,
                }
            };
            tokio::select! {
                status = child.wait(), if !watching => break status,
                _ = leader_exited => {
                    if let (Some(g), true) = (group, termed) {
                        // SAFETY: the child leads group `g` and is not reaped, so `g` is still its group.
                        unsafe { libc::kill(-g, libc::SIGKILL) };
                    }
                    break child.wait().await;
                }
                asked = rx.recv(), if asking => match asked {
                    // Not reaped yet: only this task waits for the child, and its wait has not returned.
                    Some(Stop::Kill) => {
                        group = None;
                        let _ = child.start_kill();
                        kill_at = None;
                    }
                    Some(Stop::Signal(signal)) => {
                        group = None;
                        if let Some(pid) = child.id() {
                            unsafe { libc::kill(pid as i32, signal); }
                        }
                    }
                    Some(Stop::Term(grace)) => {
                        if let Some(pid) = child.id() {
                            unsafe { libc::kill(group.map_or(pid as i32, |g| -g), libc::SIGTERM); }
                            termed = true;
                        }
                        let at = tokio::time::Instant::now() + grace;
                        kill_at = Some(kill_at.map_or(at, |k| k.min(at)));
                    }
                    None => asking = false,
                },
                _ = escalate => {
                    match group {
                        Some(g) => unsafe { libc::kill(-g, libc::SIGKILL); },
                        None => { let _ = child.start_kill(); }
                    }
                    kill_at = None;
                    asking = false;
                }
            }
        };
        let _ = done.send(status);
    });
    (Stopper(tx), exited)
}

/// An exit status as Python's `returncode`: the code, or minus the signal that ended it.
pub fn return_code(status: &std::io::Result<std::process::ExitStatus>) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    match status {
        Ok(s) => s.code().unwrap_or_else(|| -s.signal().unwrap_or(1)),
        Err(_) => -1,
    }
}

/// The runtimes this runner runs.
#[derive(Clone)]
pub enum Host {
    Claude(claude::ClaudeHost),
    Codex(codex::CodexHost),
    Gemini(gemini::GeminiHost),
    /// The gemini runtime's Antigravity harness, kept warm between turns (crate::warm).
    Antigravity(antigravity::AntigravityHost),
    MuseCode(musecode::MuseCodeHost),
    Pi(pi::PiHost),
    Cursor(cursor::CursorHost),
    Grok(grok::GrokHost),
    /// A bot in another platform's app on this Mac, through tico-appdriver (crate::appdriver).
    AppDriver(appdriver::AppDriverHost),
}

impl Host {
    pub fn track_allocation(&self, turn: std::sync::Arc<std::sync::atomic::AtomicBool>) {
        match self {
            Host::Claude(h) => h.track_allocation(turn),
            Host::Codex(h) => h.track_allocation(turn),
            Host::Gemini(h) => h.track_allocation(turn),
            Host::Antigravity(h) => h.track_allocation(turn),
            Host::MuseCode(h) => h.track_allocation(turn),
            Host::Pi(h) => h.track_allocation(turn),
            Host::Cursor(h) => h.track_allocation(turn),
            Host::Grok(h) => h.track_allocation(turn),
            Host::AppDriver(h) => h.track_allocation(turn),
        }
    }

    /// Record this harness's raw stream into `capture`; false for a harness the runner does not capture yet.
    pub fn set_capture(&self, capture: &std::sync::Arc<crate::capture::Capture>) -> bool {
        match self {
            Host::Claude(h) => h.set_capture(capture.clone()),
            Host::Codex(h) => h.set_capture(capture.clone()),
            _ => return false,
        }
        true
    }

    pub async fn start(&self) -> Result<(), HostError> {
        #[cfg(test)]
        TEST_STARTS.with(|starts| starts.set(starts.get() + 1));
        match self {
            Host::Claude(h) => h.start().await,
            Host::Codex(h) => h.start().await,
            Host::Gemini(h) => {
                h.start();
                Ok(())
            }
            Host::Antigravity(h) => h.start(),
            Host::MuseCode(h) => {
                h.start();
                Ok(())
            }
            Host::Pi(h) => {
                h.start();
                Ok(())
            }
            Host::Cursor(h) => {
                h.start();
                Ok(())
            }
            Host::Grok(h) => {
                h.start();
                Ok(())
            }
            Host::AppDriver(h) => {
                h.start();
                Ok(())
            }
        }
    }

    pub async fn stop(&self) {
        match self {
            Host::Claude(h) => h.stop().await,
            Host::Codex(h) => h.stop().await,
            Host::Gemini(h) => h.stop().await,
            Host::Antigravity(h) => h.stop().await,
            Host::MuseCode(h) => h.stop().await,
            Host::Pi(h) => h.stop().await,
            Host::Cursor(h) => h.stop().await,
            Host::Grok(h) => h.stop().await,
            Host::AppDriver(h) => h.stop().await,
        }
    }

    /// Whether the host's process may still be running. Codex, the one CLI that serves one turn per login
    /// (crate::login_turns), says until its process is reaped; the others say `alive`.
    pub fn running(&self) -> bool {
        match self {
            Host::Codex(h) => h.running(),
            _ => self.alive(),
        }
    }

    /// `stop`, and for Codex wait until its process has been reaped, even after an earlier stop was cut off.
    pub async fn stop_and_reap(&self) {
        match self {
            Host::Codex(h) => h.stop_and_reap().await,
            _ => self.stop().await,
        }
    }

    pub fn alive(&self) -> bool {
        match self {
            Host::Claude(h) => h.alive(),
            Host::Codex(h) => h.alive(),
            Host::Gemini(h) => h.alive(),
            Host::Antigravity(h) => h.alive(),
            Host::MuseCode(h) => h.alive(),
            Host::Pi(h) => h.alive(),
            Host::Cursor(h) => h.alive(),
            Host::Grok(h) => h.alive(),
            Host::AppDriver(h) => h.alive(),
        }
    }

    /// The manifest runtime and variant this host runs, for its declared capabilities.
    fn harness(&self) -> (&'static str, &'static str) {
        match self {
            Host::Claude(_) => ("claude", ""),
            Host::Codex(_) => ("codex", ""),
            Host::Gemini(_) => ("gemini", ""),
            Host::Antigravity(_) => ("gemini", "antigravity"),
            Host::MuseCode(_) => ("musecode", ""),
            Host::Pi(_) => ("pi", ""),
            Host::Cursor(_) => ("cursor", ""),
            Host::Grok(_) => ("grok", ""),
            Host::AppDriver(_) => ("grokbot", ""),
        }
    }

    /// A running turn takes new input only when the harness declares `steer`, and for Claude Code only when the
    /// installed CLI echoes its input; otherwise the input waits for the next turn.
    pub fn supports_steer(&self) -> bool {
        let (runtime, harness) = self.harness();
        crate::harness_tools::caps(runtime, harness).steer
            && match self {
                Host::Claude(h) => h.supports_steer(),
                _ => true,
            }
    }

    pub async fn start_thread(&self, bot: &str, settings: &Settings) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.start_thread(settings)),
            Host::Codex(h) => h.start_thread(bot, settings).await,
            Host::Gemini(h) => Ok(h.start_thread(settings)),
            Host::Antigravity(h) => Ok(h.start_thread(settings)),
            Host::MuseCode(h) => Ok(h.start_thread(settings)),
            Host::Pi(h) => Ok(h.start_thread(settings)),
            Host::Cursor(h) => h.start_thread(settings).await,
            Host::Grok(h) => h.start_thread(bot, settings).await,
            Host::AppDriver(h) => Ok(h.start_thread(settings)),
        }
    }

    /// A new thread started on a fork of session `from`, and whether it is one: only Claude Code forks a session;
    /// every other host starts a plain new thread.
    pub async fn start_thread_forked(
        &self,
        bot: &str,
        settings: &Settings,
        from: &str,
    ) -> Result<(String, bool), HostError> {
        match self {
            Host::Claude(h) => Ok((h.start_thread_forked(settings, from), true)),
            _ => Ok((self.start_thread(bot, settings).await?, false)),
        }
    }

    pub async fn resume_thread(
        &self,
        thread_id: &str,
        settings: &Settings,
    ) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Codex(h) => h.resume_thread(thread_id, settings).await,
            Host::Gemini(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Antigravity(h) => h.resume_thread(thread_id, settings),
            Host::MuseCode(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Pi(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Cursor(h) => Ok(h.resume_thread(thread_id, settings)),
            Host::Grok(h) => h.resume_thread(thread_id, settings).await,
            Host::AppDriver(h) => Ok(h.resume_thread(thread_id, settings)),
        }
    }

    pub async fn start_turn(
        &self,
        thread_id: &str,
        text: &str,
        effort: Option<&str>,
    ) -> Result<String, HostError> {
        match self {
            Host::Claude(h) => h.start_turn(thread_id, text, effort),
            Host::Codex(h) => h.start_turn(thread_id, text, effort).await,
            Host::Gemini(h) => h.start_turn(thread_id, text, effort),
            Host::Antigravity(h) => h.start_turn(thread_id, text, effort),
            Host::MuseCode(h) => h.start_turn(thread_id, text, effort),
            Host::Pi(h) => h.start_turn(thread_id, text, effort).await,
            Host::Cursor(h) => h.start_turn(thread_id, text, effort),
            Host::Grok(h) => h.start_turn(thread_id, text).await,
            Host::AppDriver(h) => h.start_turn(thread_id, text, effort),
        }
    }

    pub async fn steer(&self, thread_id: &str, turn_id: &str, text: &str) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => h.steer(thread_id, turn_id, text),
            Host::Codex(h) => h.steer(thread_id, turn_id, text).await,
            Host::Gemini(_) => Err(failed("Gemini runtime cannot steer a running turn")),
            Host::Antigravity(h) => h.steer(),
            Host::MuseCode(_) => Err(HostError::SteerRefused(
                "Muse Code cannot steer a running turn".into(),
            )),
            Host::Pi(h) => h.steer(thread_id, turn_id, text).await,
            Host::Cursor(_) => Err(failed("Cursor runtime cannot steer a running turn")),
            Host::Grok(_) => Err(failed("grok agent cannot steer a running turn")),
            Host::AppDriver(_) => Err(failed("a turn in a Mac app cannot be steered")),
        }
    }

    pub async fn interrupt(&self, thread_id: &str, turn_id: &str) -> Result<(), HostError> {
        match self {
            Host::Claude(h) => {
                h.interrupt(thread_id, turn_id);
                Ok(())
            }
            Host::Codex(h) => h.interrupt(thread_id, turn_id).await,
            Host::Gemini(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Antigravity(h) => {
                h.interrupt().await;
                Ok(())
            }
            Host::MuseCode(h) => {
                h.interrupt(thread_id, turn_id);
                Ok(())
            }
            Host::Pi(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Cursor(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
            Host::Grok(h) => h.interrupt(thread_id).await,
            Host::AppDriver(h) => {
                h.interrupt(turn_id);
                Ok(())
            }
        }
    }

    pub fn session_id(&self, thread_id: &str) -> String {
        match self {
            Host::Claude(h) => h.session_id(thread_id),
            _ => thread_id.to_string(),
        }
    }

    /// Whether this host says when a thread it started is up (a `thread_confirmed` event: Claude's system init,
    /// Gemini's init, pi's reply to its first prompt, Codex's first turn/started). A new thread on one is kept only then.
    pub fn confirms_threads(&self) -> bool {
        matches!(
            self,
            Host::Claude(_) | Host::Codex(_) | Host::Gemini(_) | Host::Pi(_)
        )
    }

    /// Whether a thread this host was asked to resume turned out not to be on this computer, and the harness began a
    /// new session in its place (Claude Code and Gemini find out only when the turn runs).
    pub fn restored(&self, thread_id: &str) -> bool {
        match self {
            Host::Claude(h) => h.restored(thread_id),
            Host::Gemini(h) => h.restored(thread_id),
            _ => false,
        }
    }

    /// Whether the harness says in its stream when it compacts a session (`compaction_events`), so a resumed turn
    /// may leave out what the session already holds (crate::prompt::build) and still learn when it no longer does.
    pub fn reports_compaction(&self) -> bool {
        let (runtime, harness) = self.harness();
        crate::harness_tools::caps(runtime, harness).compaction_events
            && matches!(self, Host::Claude(_) | Host::Pi(_))
    }

    /// Whether the harness compacted `thread_id` since this was last asked.
    pub fn compacted(&self, thread_id: &str) -> bool {
        match self {
            Host::Claude(h) => h.compacted(thread_id),
            Host::Pi(h) => h.compacted(thread_id),
            _ => false,
        }
    }

    /// The size in tokens of `thread_id`'s context after its last turn here (its newest model request of the bot's
    /// own, else the turn's usage), once; None when the host does not say.
    pub fn context_tokens(&self, thread_id: &str) -> Option<i64> {
        match self {
            Host::Claude(h) => h.context_tokens(thread_id),
            Host::Pi(h) => h.context_tokens(thread_id),
            _ => None,
        }
    }

    /// What a resumed turn's session is given instead of the turn's prompt if it turns out to be missing and the
    /// harness starts a new one in its place (`restored`): a new session's whole prompt.
    pub fn full_prompt(&self, thread_id: &str, text: Option<String>) {
        if let Host::Claude(h) = self {
            h.full_prompt(thread_id, text);
        }
    }

    /// What a turn whose prompt left parts out (crate::prompt::Built::restore) is given at once if the harness compacts
    /// the session during it.
    pub fn compaction_refresh(&self, thread_id: &str, text: Option<String>) {
        if let Host::Claude(h) = self {
            h.compaction_refresh(thread_id, text);
        }
    }

    /// The session a thread this host started runs as, once its harness confirmed it; None before, and on a host
    /// that does not confirm threads.
    pub fn confirmed(&self, thread_id: &str) -> Option<String> {
        match self {
            Host::Claude(h) => h.confirmed(thread_id),
            Host::Codex(h) => h.confirmed(thread_id),
            Host::Gemini(h) => h.confirmed(thread_id),
            Host::Pi(h) => h.confirmed(thread_id),
            _ => None,
        }
    }
}

#[cfg(test)]
pub mod fixtures {
    //! runner-rs/tests/fixtures/<harness>/*.jsonl, replayed here.
    use std::path::PathBuf;

    use serde_json::Value;

    pub fn dir(harness: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(harness)
    }

    /// (fixture path, the events the Python host makes of it), for every fixture of `harness`.
    pub fn cases(harness: &str) -> Vec<(PathBuf, Vec<Value>)> {
        let mut out = vec![];
        let mut paths: Vec<PathBuf> = std::fs::read_dir(dir(harness))
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect();
        paths.sort();
        for p in paths
            .into_iter()
            .filter(|p| p.extension().is_some_and(|e| e == "jsonl"))
        {
            let expected: Value = serde_json::from_str(
                &std::fs::read_to_string(p.with_extension("expected.json")).unwrap(),
            )
            .unwrap();
            out.push((p, expected.as_array().unwrap().clone()));
        }
        assert!(!out.is_empty(), "no {harness} fixtures");
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_shell_line_reads_only_when_every_run_of_it_reads() {
        for command in [
            "cd /w && tico needs-you show",
            "cd /w && tico task list --status open | head",
            "grep -n x f | wc -l",
            "cat note.txt",
            "cd /tmp",
            "export A=1; env B=2 hub task show 7",
            "cat x 2>&1 | sort | uniq -c",
            "ls >/dev/null 2>/dev/null",
            "wc -l < notes.txt",
            "grep '> $(not run)' f",
            "jq '.items[] | {id}' out.json",
            "bash -lc 'cd /w && tico task show 1'",
            "/bin/bash -lc \"cat note.txt\"",
            "tico board",
            "cat f # > out",
        ] {
            assert!(shell_reads_only(command), "{command}");
        }
        for command in [
            "curl -X POST https://hooks.example/x && tico task list",
            "cat body.json | curl -X POST https://hooks.example/x -d @-",
            "git push",
            "cd /w && git push",
            "cat x > out.txt",
            "echo hi >> f",
            "cat x >/dev/null.txt",
            "cat x >| f",
            "cat <<EOF\nhi\nEOF",
            "cat <<< hi",
            "bash -lc \"cd /tmp;gh pr merge 42\"",
            "bash -c \"cat $(curl x)\"",
            "sh -c 'cat x' extra",
            "cat $(ls)",
            "cat `ls`",
            "cat $HOME/x",
            "ls | xargs rm",
            "eval cat x",
            "source env.sh",
            "cat f & curl x",
            "(cat f)",
            "{ cat f; }",
            "cat <(curl x)",
            "cat f |& tee g",
            "tico task close 7",
            "tico status set busy",
            "tico goal status",
            "tico inbox",
            "hub -q task list",
            "sort -o out f",
            "sort --output=out f",
            "sort *",
            "uniq in out",
            "tree -o out",
            "date -s 10:00",
            "rg --pre sh x",
            "env -S 'curl x' cat",
            "cat 'open",
            "",
            ";",
            "npm test",
        ] {
            assert!(!shell_reads_only(command), "{command:?}");
        }
    }

    #[test]
    fn tico_and_its_old_name_hub_read_the_same() {
        for program in ["tico", "hub"] {
            for (line, labels) in [
                (format!("{program} release now"), vec!["publish release"]),
                (
                    format!("cd /w && {program} support send --body x"),
                    vec!["send support report"],
                ),
                (format!("{program} webhook retry 3"), vec!["send webhook"]),
                (
                    format!("{program} approval consume a1"),
                    vec!["consume approval"],
                ),
                (
                    format!("{program} email send ana@acme.example"),
                    vec!["send email"],
                ),
                (
                    format!("bash -lc '{program} release now'"),
                    vec!["publish release"],
                ),
                (format!("{program} task list"), vec![]),
            ] {
                assert_eq!(shell_outside_actions(&line), labels, "{line}");
            }
            assert!(shell_reads_only(&format!("cd /w && {program} task list")));
            assert!(shell_reads_only(&format!(
                "bash -lc '{program} needs-you show'"
            )));
            assert!(!shell_reads_only(&format!("{program} task close 7")));
            assert!(!shell_reads_only(&format!("{program} -q task list")));
            assert_eq!(
                command_program(&format!("cd /w && {program} task close 7f3a")),
                format!("{program} task close")
            );
            assert_eq!(
                command_program(&format!("/usr/local/bin/{program} message send ops x")),
                format!("{program} message send")
            );
        }
        assert_eq!(
            command_program("tico-computer restart all"),
            "tico-computer"
        );
    }

    #[test]
    fn every_hub_read_path_is_a_leaf_command_path() {
        for path in HUB_READ_PATHS {
            assert!(HUB_PATHS.contains(path), "{path} is not a hub command path");
            let below = format!("{path} ");
            assert!(
                !HUB_PATHS.iter().any(|p| p.starts_with(&below)),
                "{path} is a group"
            );
            assert_eq!(
                hub_summary(
                    &std::iter::once("hub")
                        .chain(path.split(' '))
                        .map(String::from)
                        .collect::<Vec<_>>()
                ),
                format!("hub {path}")
            );
        }
    }

    #[test]
    fn a_tool_summary_names_the_file_or_program_but_never_the_arguments() {
        assert_eq!(
            tool_summary(
                &json!({"command": "curl -H 'Authorization: Bearer sk-live-1' https://api.example.com"})
            ),
            "curl"
        );
        assert_eq!(
            tool_summary(
                &json!({"command": "TOKEN=sk-live-1 /usr/bin/python3 run.py --key sk-live-1"})
            ),
            "python3"
        );
        assert_eq!(
            command_program("/bin/bash -lc 'gh api -H \"Authorization: token x1\" /user'"),
            "gh"
        );
        assert_eq!(
            tool_summary(
                &json!({"command": "TOKEN=\"Bearer sk-live-abc\" curl https://api.example.com"})
            ),
            "curl"
        );
        assert_eq!(
            tool_summary(&json!({"command": "AUTH='x sk-live-abc' ./run.sh"})),
            "run.sh"
        );
        assert_eq!(
            tool_summary(&json!({"command": "bash -c \"TOKEN='a sk-live-abc' curl x\""})),
            "curl"
        );
        assert_eq!(
            tool_summary(&json!({"command": "TOKEN='sk-live-abc curl"})),
            ""
        );
        assert_eq!(
            tool_summary(&json!({"command": "TOKEN=$(echo sk-live-abc) curl x"})),
            ""
        );
        assert_eq!(
            tool_summary(&json!({"command": "TOKEN=`echo sk-live-abc` curl x"})),
            ""
        );
        assert_eq!(
            tool_summary(&json!({"command": "TOKEN=${T:-a sk-live-abc} curl x"})),
            ""
        );
        assert_eq!(
            tool_summary(&json!({"command": "curl -H \"X: $(cat key)\" x"})),
            "curl"
        );
        assert_eq!(
            tool_summary(&json!({"file_path": "/work/note.txt", "content": "secret body"})),
            "/work/note.txt"
        );
        assert_eq!(
            tool_summary(
                &json!({"description": "count words", "prompt": "Count the words", "subagent_type": "general-purpose"})
            ),
            ""
        );
        assert_eq!(
            tool_summary(
                &json!({"query": "private question", "url": "https://x.example/?token=1"})
            ),
            ""
        );
    }

    #[test]
    fn a_compound_command_names_the_work_and_never_the_arguments() {
        assert_eq!(
            command_program("cd /repo && tico task close --id 42 --reason 'sk-live-1'"),
            "tico task close"
        );
        assert_eq!(
            command_program(
                "export HUB_TOKEN=sk-live-1; /usr/local/bin/tico task create --title sk-live-1"
            ),
            "tico task create"
        );
        assert_eq!(
            command_program("tico task note sk-live-1 close"),
            "tico task"
        );
        assert_eq!(
            command_program("cd web && npm run build && tico task list"),
            "tico task list"
        );
        assert_eq!(command_program("set -e\nnpm test | tee out.log"), "set");
        assert_eq!(command_program("cargo test 2>&1 | tail"), "cargo");
        assert_eq!(command_program("cd /tmp"), "cd");
        assert_eq!(command_program("(cd web && npm test)"), "");
        assert_eq!(command_program("sh -c 'cd x && cargo test'"), "cargo");
        assert_eq!(
            command_program("tico task create --title \"a && tico task close\""),
            "tico task create"
        );
        assert_eq!(
            command_program("tico message send botops please check the release"),
            "tico message send"
        );
        assert_eq!(command_program("FOO=1 && curl x"), "curl");
        assert_eq!(command_program("FOO=1 curl x"), "curl");
        assert_eq!(command_program("X=$(a && sk-live-1) hub task list"), "");
        assert_eq!(
            tool_summary(&json!({"command": "cd web && tico task list --token sk-live-1"})),
            "tico task list"
        );
    }

    #[test]
    fn no_line_shows_its_private_words_whatever_it_quotes_redirects_or_tests() {
        for line in [
            r#"export X="$(printf '%s' "a; private-token")""#,
            "cd /repo <<'EOF'\nprivate-token\nEOF\nnpm test",
            "cd /repo && >/tmp/customer-123.json printf ok",
            "cd /repo && > /tmp/customer-123.json printf ok",
            "cd /tmp && >|private-token printf ok",
            "cd /tmp && [[ a == a || private-token == other ]]",
            "(( 1 || private_token ))",
            "cd /repo # note; private-token\nnpm test",
        ] {
            let shown = command_program(line);
            assert!(
                !shown.contains("private") && !shown.contains("customer"),
                "{line:?} showed {shown:?}"
            );
        }
        assert_eq!(
            command_program("cd /repo <<'EOF'\nprivate-token\nEOF\nnpm test"),
            "cd"
        );
        assert_eq!(
            command_program("cd /repo && >/tmp/customer-123.json printf ok"),
            ""
        );
        assert_eq!(command_program(">|private-token printf ok"), "");
        assert_eq!(command_program("cd /tmp && >|private-token printf ok"), "");
        assert_eq!(command_program("(( 1 || private_token ))"), "");
        assert_eq!(
            command_program("cd /w && tico task close 7f3a"),
            "tico task close"
        );
        assert_eq!(command_program("cd /w && npm test"), "npm");
    }

    #[test]
    fn a_shell_comment_is_in_no_run() {
        assert_eq!(
            command_program("cd /repo # note; private-token\nnpm test"),
            "npm"
        );
        assert_eq!(command_program("cd /repo # note; private-token"), "cd");
        assert_eq!(command_program("echo ok # x && tico task close"), "echo");
        assert_eq!(
            command_program("cd x; a#b && tico task list"),
            "tico task list"
        );
        assert_eq!(
            command_program("echo '# x'; tico task list"),
            "tico task list"
        );
        assert_eq!(command_program("sk-live-1$x y"), "");
    }

    #[test]
    fn a_hub_summary_stops_at_the_first_word_that_does_not_continue_a_command_path() {
        assert_eq!(
            command_program("tico message send finance \"private question\""),
            "tico message send"
        );
        assert_eq!(command_program("tico history review"), "tico history");
        assert_eq!(command_program("tico task close 7f3a"), "tico task close");
        assert_eq!(
            command_program("tico needs-you show"),
            "tico needs-you show"
        );
        assert_eq!(
            command_program("tico bot status history"),
            "tico bot status history"
        );
        for action in [
            "archive",
            "unarchive",
            "mark-read",
            "mark-unread",
            "label",
            "unlabel",
        ] {
            assert_eq!(
                command_program(&format!(
                    "tico email {action} ana@acme.example private-label private-id"
                )),
                format!("tico email {action}")
            );
        }
        assert_eq!(command_program("hub nosuch command"), "hub");
    }

    #[test]
    fn a_hub_line_inside_a_heredoc_body_is_text() {
        assert_eq!(
            command_program("cat <<'EOF'\nhub approval approve\nEOF"),
            "cat"
        );
        assert_eq!(
            command_program("cat > /tmp/x <<EOF && echo ok\nhub task close 7f3a\nEOF"),
            "cat"
        );
        assert_eq!(
            command_program("tico automation create --file - <<'EOF'\n{\"name\": \"x\"}\nEOF"),
            "tico automation create"
        );
        assert_eq!(
            command_program(
                "cd /w && tico automation create --file - <<'EOF'\n{\"name\": \"x\"}\nEOF"
            ),
            "tico automation create"
        );
        assert_eq!(command_program("<<'EOF'\nhub approval approve\nEOF"), "");
        assert_eq!(command_program("<<EOF\ncustomer-private\nEOF"), "");
        assert_eq!(command_program(">'>' printf customer-private"), "");
        assert_eq!(command_program("cd /w && >\\> printf customer-private"), "");
        assert_eq!(
            command_program("cd /w && >'>' tico task close customer-private"),
            ""
        );
        assert_eq!(
            command_program("TOKEN=customer-private >'>' printf customer-private"),
            ""
        );
        assert_eq!(command_program("2> >(printf customer-private) true"), "");
        assert_eq!(
            command_program("/bin/zsh -lc 'cd /w && tico needs-you show'"),
            "tico needs-you show"
        );
        assert_eq!(
            command_program("bash --norc /tmp/customer-private.sh"),
            "bash"
        );
        assert_eq!(command_program("bash -c-x 'customer-private'"), "bash");
        assert_eq!(command_program("bash -oc /tmp/customer-private.sh"), "bash");
        assert_eq!(
            command_program("bash;<<\\'\n-c\n'customer-private\n'"),
            "bash"
        );
        assert_eq!(
            command_program("bash; -c /tmp/customer-private.json"),
            "bash"
        );
        assert_eq!(
            command_program("bash \"-\\c\" /tmp/customer-private.json"),
            "bash"
        );
        assert_eq!(
            command_program("bash \n-c /tmp/customer-private.json"),
            "bash"
        );
        assert_eq!(command_program("NOTE=\rcustomer-private"), "");
        assert_eq!(command_program("NOTE=\u{a0}customer-private"), "");
        assert_eq!(
            command_program("bash -c \"NOTE=\\ customer-private /usr/bin/true\""),
            ""
        );
        assert_eq!(
            command_program("bash -c \"NOTE=\\$x customer-private\""),
            ""
        );
        assert_eq!(command_program("bash -c \r customer-private"), "bash");
        assert_eq!(command_program("bash -c \u{a0} customer-private"), "bash");
        assert_eq!(
            command_program("bash -c \"NOTE='$(printf %b \\\\047\\\\040/usr/bin/true\\\\040\\\\047)' customer-private\""),
            "bash"
        );
        assert_eq!(
            command_program("bash -c \"NOTE='`printf x`' customer-private\""),
            "bash"
        );
        assert_eq!(
            command_program("bash -lc 'cd /w && tico needs-you show $X'"),
            "tico needs-you show"
        );
        assert_eq!(
            command_program("bash -c \"cd /w && tico \\\nneeds-you show\""),
            "tico needs-you show"
        );
    }

    #[test]
    fn a_heredoc_substitution_or_redirection_never_shows_its_arguments() {
        // Nested quotes inside a substitution: the line is one run and reads no further than its first program.
        let nested = command_program(r#"export X="$(printf '%s' "a; private-token")""#);
        assert!(nested.is_empty() || nested == "export", "{nested}");
        assert!(!nested.contains("private-token"));
        // A heredoc body is no run.
        assert_eq!(
            command_program("cd /repo <<'EOF'\nprivate-token\nEOF\nnpm test"),
            "cd"
        );
        // A redirection is skipped to the program behind it, whether its target is attached or the next word.
        assert_eq!(
            command_program("cd /repo && >/tmp/customer-123.json printf ok"),
            ""
        );
        assert_eq!(
            command_program("cd /repo && > /tmp/customer-123.json printf ok"),
            ""
        );
        assert_eq!(command_program("cd /w && 2>&1 npm test"), "");
        // A program word is a plain path, and a program name is at most 40 characters.
        assert_eq!(command_program("$HOME/bin/tool --x"), "");
        assert_eq!(command_program(&format!("{} x", "a".repeat(41))), "");
        // A hub command in a later run still reads; a setup run before other work is skipped.
        assert_eq!(
            command_program("cd /w && tico task close 7f3a"),
            "tico task close"
        );
        assert_eq!(command_program("cd /w && npm test"), "npm");
    }

    #[test]
    fn a_setup_run_before_the_work_is_skipped_and_its_arguments_never_show() {
        assert_eq!(
            command_program(
                "cd /home/user/workspace/bot-assistant && tico task list --status open"
            ),
            "tico task list"
        );
        assert_eq!(
            command_program("cd \"/home/user/My Repo\" && git status"),
            "git"
        );
        assert_eq!(command_program("cd /repo && npm test"), "npm");
        assert_eq!(command_program("cd /repo;npm test"), "npm");
        assert_eq!(command_program("cd /repo\nnpm test"), "npm");
        assert_eq!(command_program("cd /repo # note\nnpm test"), "npm");
        assert_eq!(
            command_program("cd /repo && export TOKEN=sk-live-1 && cargo test"),
            "cargo"
        );
        assert_eq!(
            command_program("export HUB_TOKEN=sk-live-1 && npm test"),
            "npm"
        );
        assert_eq!(
            command_program("export HUB_TOKEN=sk-live-1 && tico task create --title x"),
            "tico task create"
        );
        assert_eq!(
            command_program("env HUB_TOKEN=sk-live-1 cargo test"),
            "cargo"
        );
        assert_eq!(
            command_program("env HUB_TOKEN=sk-live-1 tico task list --token x"),
            "tico task list"
        );
        assert_eq!(
            command_program("cd /w && env HUB_TOKEN=sk-live-1 tico task close 7f3a"),
            "tico task close"
        );
        assert_eq!(command_program("cd /w && env -i A=1 npm test"), "npm");
        assert_eq!(command_program("source ~/.env.sh && npm test"), "npm");
        assert_eq!(
            command_program(". ./secret-env && tico task close 7f3a"),
            "tico task close"
        );
        assert_eq!(
            command_program("cd /w && source a.sh b.sh && npm test"),
            "source"
        );
        assert_eq!(command_program("env -u sk-live-1 npm test"), "env");
        assert_eq!(command_program("cd /w && env -u sk-live-1 npm test"), "env");
        assert_eq!(
            command_program("cd /w && export A=1 B=2 && npm test"),
            "npm"
        );
        assert_eq!(command_program("cd /w && npm test > out.log"), "npm");
        assert_eq!(command_program("cd /w"), "cd");
        assert_eq!(command_program("cd /w && export A=1"), "export");
        assert!(
            !command_program("cd /private-customer && export K=sk-live-1 && npm test")
                .contains("private")
        );
    }

    #[test]
    fn a_new_message_starts_a_paragraph_and_pieces_of_one_message_join_with_nothing() {
        let mut reply = String::new();
        assert_eq!(
            add_piece(&mut reply, true, "Holding until "),
            "Holding until "
        );
        assert_eq!(
            add_piece(&mut reply, false, "it recovers. "),
            "it recovers. "
        );
        assert_eq!(add_piece(&mut reply, true, "No change."), "\n\nNo change.");
        assert_eq!(reply, "Holding until it recovers.\n\nNo change.");
    }

    #[test]
    fn the_python_classifiers_agree() {
        assert!(is_limit("You've hit your usage limit"));
        assert!(is_limit(
            "You've hit your session limit · resets 12:40am (UTC)"
        ));
        assert!(is_limit("HTTP 503 overloaded"));
        assert!(!is_limit("all good"));
        assert!(is_limit(
            "Claude AI monthly spend limit reached. Raise it in your account settings."
        ));
        assert!(is_limit("API error 429: Subscription quota exhausted. Your usage window resets at 2026-10-19T00:00:00Z. (rate_limit_error)"));
        assert!(!is_limit("Draft the monthly spend report"));
        // An API key's quota or billing refusal, as Codex and the OpenAI API word it.
        assert!(is_limit(
            "Quota exceeded. Check your plan and billing details."
        ));
        assert!(is_limit("You exceeded your current quota, please check your plan and billing details. (insufficient_quota)"));
        assert!(is_limit(
            r#"{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}"#
        ));
        assert!(!is_limit("Raise the storage quota for the team drive"));
        assert!(!is_limit("Quota: 40 of 100 seats used"));
        assert!(!is_limit(
            "write /tmp/out: Disk quota exceeded (os error 122)"
        ));
        // Capacity is a limit (a fallback may take the turn) but not quota; the plan's quota stays quota.
        assert!(
            is_limit("Selected model is at capacity. Please try a different model.")
                && is_capacity("Selected model is at capacity. Please try a different model.")
        );
        assert!(is_capacity("HTTP 503 overloaded"));
        // An API's structured refusal ("overloaded_error"): the underscore is a word character, so it needs its own match.
        let structured = r#"API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Server busy"}}"#;
        assert!(is_limit(structured) && is_capacity(structured));
        assert!(!is_capacity(
            "Quota exceeded. Check your plan and billing details."
        ));
        assert!(!is_capacity("API error 429: Subscription quota exhausted. Your usage window resets at 2026-10-19T00:00:00Z. (rate_limit_error)"));
        assert!(!is_capacity("You've hit your usage limit"));
        assert!(is_auth_retryable("OAuth: failed to refresh OAuth token"));
        assert!(!is_auth_retryable(
            "unexpected status 401 Unauthorized: Invalid API key"
        ));
        assert!(is_auth_rejected("Invalid API key · Please run /login"));
        // Codex with no login at all (codex 0.167, cli_contract.rs): nothing was sent, so no retry can help.
        let no_login = "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header, url: https://api.openai.com/v1/responses";
        assert!(is_auth_rejected(no_login) && !is_auth_retryable(no_login));
        assert_eq!(
            rejection_reason("x\nInvalid API key sk-ant-abcdefghijk"),
            "Invalid API key [redacted]"
        );
        assert_eq!(compact_at(&json!("120000")), 120000);
        assert_eq!(compact_at(&json!(0)), DEFAULT_COMPACT_AT);
    }

    #[test]
    fn a_bot_key_problem_is_its_own_grant_and_only_for_a_bot_that_needs_one() {
        let grants: Map<String, Value> = json!({"has": ["OPENROUTER_API_KEY"]})
            .as_object()
            .cloned()
            .unwrap();
        let pi = |model: &str| json!({"runtime": "pi", "harness": "pi", "model": model});
        assert_eq!(bot_key_problem("pi", &pi("kimi-k3"), "has", &grants), None);
        assert_eq!(
            bot_key_problem("pi", &pi("kimi-k3"), "none", &grants),
            Some("OpenRouter API key required".into())
        );
        // A Tico model signs in with the company's gateway key, so it needs no grant of its own.
        assert_eq!(
            bot_key_problem("pi", &pi("tico-medium-1.0"), "none", &grants),
            None
        );
        let gemini = json!({"runtime": "gemini", "harness": "gemini", "model": "gemini-3.7-flash"});
        assert_eq!(
            bot_key_problem("gemini", &gemini, "has", &grants),
            Some("Gemini API key required".into())
        );
        // Antigravity runs on the Gemini runtime with its own sign-in, not a key here.
        let antigravity =
            json!({"runtime": "gemini", "harness": "antigravity", "model": "gemini-3.7-flash"});
        assert_eq!(
            bot_key_problem("gemini", &antigravity, "none", &grants),
            None
        );
        assert_eq!(
            bot_key_problem("claude", &json!({"runtime": "claude"}), "none", &grants),
            None
        );
    }
}

// Per-host answers behind a capability: generic code reads the manifest's capability and asks here, so the adapters
// stay the one place that knows a CLI by name.

/// Native goals and built-in commands of one installed harness (goals.capabilities): Claude Code by version, Codex by
/// the protocol its app-server describes; every other host has none.
pub async fn probe_goals(
    runtime: &str,
    exe: &std::path::Path,
    version: &str,
) -> (bool, Vec<Value>) {
    let (mut supported, mut commands) = (false, vec![]);
    match runtime {
        "claude" => {
            supported =
                crate::hosts::claude::version_tuple(version).is_some_and(|v| v >= (2, 1, 212));
            if supported {
                commands = vec![
                    crate::goals::command("compact", "Compact this conversation", "[instructions]"),
                    crate::goals::command("clear", "Clear the harness conversation", ""),
                    crate::goals::command("model", "Change the harness model", "[model]"),
                ];
            }
        }
        "codex" => {
            // The installed protocol, rather than a version guess, determines native goal support.
            let dir = std::env::temp_dir().join(format!(
                "tico-goal-schema-{}",
                uuid::Uuid::new_v4().simple()
            ));
            if std::fs::create_dir_all(&dir).is_ok() {
                let mut cmd = tokio::process::Command::new(exe);
                cmd.args([
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                ])
                .arg(&dir);
                cmd.stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .kill_on_drop(true);
                if let Ok(Ok(status)) =
                    tokio::time::timeout(std::time::Duration::from_secs(10), cmd.status()).await
                {
                    if status.success() {
                        let schema = dir.join("v2");
                        supported = schema.join("ThreadGoalSetParams.json").exists();
                        if schema.join("ThreadCompactStartParams.json").exists() {
                            commands.push(crate::goals::command(
                                "compact",
                                "Compact this conversation",
                                "",
                            ));
                        }
                        if schema.join("ReviewStartParams.json").exists() {
                            commands.push(crate::goals::command(
                                "review",
                                "Review changes",
                                "[instructions]",
                            ));
                        }
                    }
                }
                let _ = std::fs::remove_dir_all(&dir);
            }
        }
        _ => {}
    }
    (supported, commands)
}

/// `sign_in_status.check = "bot-key"`: every bot that runs this host holds its key grant.
pub fn bot_key_sign_in(runtime: &str, assignments: &[Value]) -> (&'static str, String) {
    match runtime {
        "gemini" => gemini::sign_in(assignments),
        "pi" => pi::sign_in(assignments),
        _ => ("unknown", "This harness's key cannot be checked".into()),
    }
}

/// `sign_in_status.check = "bot-key"`: the problem one bot has with its own key on `runtime`, or None when it holds the
/// key its runtime reads or needs none here. Only that bot's readiness depends on it, never another bot's grant.
pub fn bot_key_problem(
    runtime: &str,
    config: &Value,
    bot: &str,
    grants: &Map<String, Value>,
) -> Option<String> {
    let (key, label) = match runtime {
        "gemini" => (
            gemini::uses_gemini_cli(config).then_some("GEMINI_API_KEY")?,
            "Gemini",
        ),
        "pi" => (
            pi::needs_grant(config).then_some("OPENROUTER_API_KEY")?,
            "OpenRouter",
        ),
        _ => return None,
    };
    (!gemini::has_grant_in(grants, bot, key)).then(|| format!("{label} API key required"))
}

/// Hand a host's default computer-login store to the account that runs its CLI. Profile homes have their own handoff.
pub fn prepare_login_home(runtime: &str, env: &Env) {
    match runtime {
        "grok" => grok::prepare_home(env),
        _ => {}
    }
}

/// Points `env` at the host's own config folder (`own_config_home`), so a login or status check sees the login its
/// turns use rather than the operator's. False for a host without one.
pub fn use_own_config_home(
    runtime: &str,
    profile: Option<&crate::profiles::Profile>,
    env: &mut Env,
) -> bool {
    match runtime {
        "musecode" => {
            env.remove("MUSE_AUTH_PATH");
            env.insert(
                "XDG_CONFIG_HOME".into(),
                musecode::config_home(profile).display().to_string(),
            );
            env.insert(
                "XDG_DATA_HOME".into(),
                musecode::data_home(profile).display().to_string(),
            );
            true
        }
        _ => false,
    }
}

/// A runtime's version the probe just read, for a host that keeps per-version facts (Claude Code's features): read
/// here, when the version is new, so a turn starts no process for them.
pub async fn note_version(runtime: &str, version: &str) {
    if runtime == "claude" {
        claude::note_version(&[crate::harness_tools::executable_for(runtime)], version).await;
    }
}

/// A folder the host's turns write that the bot user cannot (Muse Code's data home), as a readiness problem.
pub fn data_problem(runtime: &str, profile: Option<&crate::profiles::Profile>) -> Option<String> {
    match runtime {
        "musecode" => musecode::data_problem(&musecode::data_home(profile)),
        _ => None,
    }
}

/// `sign_in_status.check = "file"`: whether the host's login file is there in `env` (None: not readable).
pub fn file_signed_in(runtime: &str, env: &Env) -> Option<bool> {
    match runtime {
        "musecode" => musecode::signed_in(&musecode::auth_file(env)),
        "grok" => grok::signed_in(env),
        _ => None,
    }
}

/// A file-backed host can distinguish a subscription from a saved API-key login.
pub fn file_sign_in_detail(runtime: &str, env: &Env) -> Option<&'static str> {
    match runtime {
        "grok" => grok::sign_in_detail(env),
        _ => None,
    }
}

/// Why the host's login file cannot be used, when `file_signed_in` gives no answer for a reason the person can fix.
pub fn file_problem(runtime: &str, env: &Env) -> Option<String> {
    match runtime {
        "musecode" => musecode::store_problem(&musecode::auth_file(env)),
        _ => None,
    }
}

/// `model_list = "host"`: the models the host offers (for a profile's own login where that matters).
pub fn models(runtime: &str, profile: Option<&crate::profiles::Profile>) -> Vec<String> {
    match runtime {
        "codex" => crate::readiness::codex_models(profile.and_then(|p| p.home("codex"))),
        "claude" => crate::readiness::CLAUDE_MODELS
            .iter()
            .map(|s| s.to_string())
            .collect(),
        "gemini" => gemini::MODELS.iter().map(|s| s.to_string()).collect(),
        "musecode" => musecode::MODELS.iter().map(|s| s.to_string()).collect(),
        "pi" => pi::model_names(),
        "cursor" => cursor::model_names(),
        _ => vec![],
    }
}

/// Whether a host's stored token has expired (`serial_cold_start`), so the first turn refreshes it.
pub fn token_cold(runtime: &str, profile: Option<&crate::profiles::Profile>) -> bool {
    match runtime {
        "claude" => crate::profiles::claude_token_cold(profile),
        _ => false,
    }
}
