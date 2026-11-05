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
mod native;
pub mod musecode;
mod oneshot;
pub mod pi;
#[cfg(test)]
mod isolationtests;
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
        if state.allocated { turn.store(true, std::sync::atomic::Ordering::SeqCst); }
        state.turn = Some(turn);
    }

    fn mark(&self) {
        let mut state = self.0.lock().unwrap();
        state.allocated = true;
        if let Some(turn) = &state.turn { turn.store(true, std::sync::atomic::Ordering::SeqCst); }
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
            r"(?i)incorrect api key|invalid[ _-]?(?:x-)?api[ _-]?key|api key (?:is )?(?:invalid|revoked|expired)|not logged in|please run /login|invalid authentication credentials|authentication_error|oauth session expired and could not be refreshed|missing meta credentials",
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
    let line = text.lines().map(str::trim).find(|row| is_auth_rejected(row)).map(String::from).unwrap_or_default();
    let line = if line.is_empty() { text.trim().to_string() } else { line };
    secretish_re().replace_all(&line, "[redacted]").chars().take(200).collect()
}

pub const DEFAULT_COMPACT_AT: i64 = 250_000;

/// A bot's "Compact at" in tokens: its own whole number, else DEFAULT_COMPACT_AT.
pub fn compact_at(value: &Value) -> i64 {
    let n = match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    };
    match n {
        Some(t) if t > 0 => t,
        _ => DEFAULT_COMPACT_AT,
    }
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
    /// A bot with branches: Claude reads only the project's settings and MCP servers (hosts/claude.py).
    pub shared: bool,
    /// The bot's own MCP servers from bot.yaml, this harness's transports only (crate::perbot::mcp_for_run).
    pub mcp_servers: Vec<Value>,
    /// How long a Claude turn that has answered may wait on nothing but its background shells before it ends and
    /// leaves them running (`background_wait_s`); None waits for them, as before.
    pub background_wait: Option<std::time::Duration>,
    /// A capacity continuation must fail if its saved session is missing; rebuilding context may repeat work.
    pub require_existing_session: bool,
    /// Packet-only tidy turns have no checkout tools or Hub MCP server.
    pub packet_only: bool,
}

/// `background_wait_s` in the bot's run config: seconds, 0 for off; five minutes when unset.
pub const BACKGROUND_WAIT_S: f64 = 300.0;

pub fn background_wait(config: &Value) -> Option<std::time::Duration> {
    let v = &config["background_wait_s"];
    let s = v.as_f64().or_else(|| v.as_str().and_then(|t| t.trim().parse().ok())).unwrap_or(BACKGROUND_WAIT_S);
    (s > 0.0 && s.is_finite()).then(|| std::time::Duration::from_secs_f64(s))
}

const HUB_MCP_ENV: &[&str] = &["HUB_API_URL", "HUB_TOKEN", "HUB_BOT", "HUB_EMPLOYEE", "HUB_DIR"];

/// The hub as a stdio MCP server for one turn (`hub_mcp_server`), or None outside a turn.
pub fn hub_mcp_server(env: &Env) -> Option<Value> {
    if env.get("HUB_API_URL").is_none_or(|v| v.is_empty()) || env.get("HUB_TOKEN").is_none_or(|v| v.is_empty()) {
        return None;
    }
    let mut vars = Map::new();
    for k in HUB_MCP_ENV {
        if let Some(v) = env.get(*k).filter(|v| !v.is_empty()) {
            vars.insert(k.to_string(), json!(v));
        }
    }
    let script = crate::env::root().join("clients").join("hubmcp.py");
    Some(json!({"command": crate::env::python().display().to_string(), "args": [script.display().to_string()], "env": vars}))
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
    let text = |k: &str| input.get(k).and_then(Value::as_str).filter(|s| !s.trim().is_empty());
    if let Some(path) = ["file_path", "notebook_path", "path"].iter().find_map(|k| text(k)) {
        return clip(path);
    }
    text("command").map(command_program).unwrap_or_default()
}

/// Only fixed operation labels leave the computer. Commands can contain private text and credentials,
/// while a program-only display summary cannot distinguish `gh pr view` from `gh pr merge`.
pub fn shell_outside_actions(command: &str) -> Vec<&'static str> {
    static RULES: OnceLock<Vec<(&'static str, Regex)>> = OnceLock::new();
    let rules = RULES.get_or_init(|| [
        ("merge pull request", r"\bgh\s+(?:pr\s+merge\b|api\b[^\n]*\bmerg(?:e|ed|ing)\b)"),
        ("publish release", r"\bgh\s+release\s+create\b|\bhub\s+release\s+now\b"),
        ("publish package", r"\b(?:npm|cargo)\s+publish\b|\btwine\s+upload\b|\bdocker\s+(?:image\s+)?push\b"),
        ("consume approval", r"\b(?:hub\s+)?approval\s+consume\b"),
        ("send email", r"\b(?:hub\s+)?(?:mail|email)\s+send\b|\bsendmail\b"),
        ("send webhook", r"\b(?:hub\s+)?webhook\s+(?:send|retry|recover)\b"),
        ("send support report", r"\bhub\s+support\s+send\b"),
        ("make payment", r"\bstripe\s+(?:payments|payment_intents)\s+(?:create|confirm)\b"),
    ].into_iter().map(|(label, pattern)| (label, Regex::new(&format!("(?i){pattern}")).unwrap())).collect());
    // Normalize shell quoting as well as scanning the raw command: quoted subcommands and nested shell
    // wrappers must retain their action. Matching mentions can conservatively hold; it never exports them.
    let normalized = shell_words(command).map(|words| words.join(" ")).unwrap_or_default();
    rules.iter().filter(|(_, pattern)| pattern.is_match(command) || pattern.is_match(&normalized))
        .map(|(label, _)| *label).collect()
}

/// Every command path the hub CLI parses (clients/hubcli.py), its aliases and its top-level renames included, as
/// space-joined words such as `task close` or `needs-you show`. backend/tests/test_hub_command_tree.py fails when
/// this list and the CLI differ, so a command added or renamed there is added here too.
const HUB_PATHS: &[&str] = &[
    "ack", "agent", "agent pair", "agent pair approve", "agent pair decline", "agent pair show", "answer",
    "alerts", "alerts company", "alerts confirm", "alerts remove", "alerts set", "alerts show", "alerts test",
    "alerts verify",
    "api", "approval", "approval approve", "approval decline", "approval list", "approval read-back",
    "approval request", "approval show", "approvals", "approvals approve", "approvals decline",
    "approvals list", "approvals read-back", "approvals request", "approvals show", "ask", "assistant",
    "assistant change", "assistant change list", "assistant change restore", "assistant learn",
    "assistant learned", "assistant proposals", "assistant propose", "assistant read", "assistant send",
    "assistant withdraw", "audiences", "audiences list", "audit", "automation", "automation create",
    "automation delete", "automation discard", "automation guide", "automation held", "automation inbox",
    "automation list", "automation question-sets", "automation recent-runs", "automation restore",
    "automation run", "automation runs", "automation show", "automation stats", "automation template",
    "automation templates", "automation update", "automation versions", "automations", "automations create",
    "automations delete", "automations discard", "automations guide", "automations held", "automations inbox",
    "automations list", "automations question-sets", "automations recent-runs", "automations restore",
    "automations run", "automations runs", "automations show", "automations stats", "automations template",
    "automations templates", "automations update", "automations versions", "batch", "board", "bot",
    "bot access", "bot archive", "bot branch", "bot browser-exit", "bot budget", "bot check", "bot check-names",
    "bot copy", "bot create", "bot go-live", "bot keep-tasks-going", "bot mail", "bot model", "bot onboarded",
    "bot owners", "bot pause", "bot place", "bot profile", "bot ready", "bot recent", "bot register",
    "bot release", "bot repo-create", "bot repos", "bot restore", "bot resume", "bot scope", "bot set",
    "bot setup-done", "bot show", "bot status", "bot status history", "bot status list", "bot status set",
    "bot suggest-to-original", "bot task-model", "bot turns-at-once", "bot update", "bot update-from-original",
    "bots", "bots access", "bots archive", "bots branch", "bots browser-exit", "bots budget", "bots check",
    "bots check-names", "bots copy", "bots create", "bots go-live", "bots keep-tasks-going", "bots mail",
    "bots model", "bots owners", "bots pause", "bots place", "bots profile", "bots ready", "bots recent",
    "bots release", "bots repo-create", "bots repos", "bots restore", "bots resume", "bots scope",
    "bots setup-done", "bots show", "bots status", "bots status history", "bots status list", "bots status set",
    "bots suggest-to-original", "bots task-model", "bots turns-at-once", "bots update",
    "bots update-from-original", "brand", "brand check", "brand draft", "brand publish", "brand show",
    "brand summary", "brief", "calendar", "calendar list", "calendar schedule", "calendar status",
    "calendar upcoming", "call", "call bot", "call bot add", "call bot remove", "call end", "call join",
    "call leave", "call list", "call retry", "call show", "call start", "call token", "card", "card add",
    "card list", "card remove", "card update", "catalog", "changelog", "changelog list", "changelog mark-read",
    "chat", "chat goal", "chat send", "classify", "company-page", "computer", "computer browser-exit",
    "computer list", "computer performance", "computer turns-at-once", "computers", "connections",
    "connections list", "context", "context search", "context show", "conversation", "conversation show",
    "credential", "credential delete", "credential grant", "credential import", "credential list",
    "credential request", "credential revoke", "credential set", "db", "decision", "decision ask",
    "decision outcome", "decision readout", "decisions", "doc", "doc archive", "doc ask", "doc ask-status",
    "doc fetch", "doc history", "doc link-list", "doc list", "doc read", "doc search", "doc write", "docs",
    "docs links", "editions", "editions list", "editions show", "email", "email cancel", "email inbox", "email mailboxes",
    "email message", "email policy", "email search", "email sends", "email thread", "event-types", "external", "external sync",
    "feedback", "feedback give", "feedback list", "feedback markers", "feedback reasons", "feedback remove",
    "feedback rule", "feedback rule-draft", "file", "file approved", "file archive", "file delete",
    "file import", "file link", "file list", "file publish", "file read", "file review", "file search",
    "file touch", "files", "files add-link", "finance", "finance act", "finance action", "finance card-fill",
    "finance inbox", "finance metric", "finance report", "finance search", "fleet", "fleet-check", "gateway",
    "gateway catalog", "gateway fetch", "gateway image", "gateway search", "github", "github create-bot-repo",
    "goal", "goal auto", "goal checkin", "goal checkin list", "goal checkin-list", "goal checkins",
    "goal company", "goal create", "goal list", "goal needs-you", "goal read-back", "goal refresh", "goal save",
    "goal show", "goal status", "goal strategy-bot", "goal strategy-bot-decide",
    "goal strategy-bot-level3-offer", "goal strategy-bot-log", "goal strategy-bot-merge", "goal update",
    "goals", "grokbot", "grokbot sync", "group", "group access", "group delete", "group list", "group update",
    "growth", "growth channel", "growth channel add", "growth channel list", "growth channel retire",
    "growth channel show", "growth channel update", "growth cohort", "growth cohort list", "growth cohort set",
    "growth economics", "growth kpis", "growth numbers", "growth settings", "growth source",
    "growth source add", "growth source list", "growth source retire", "growth source update", "gtm",
    "gtm show", "gtm sources", "gtm summary", "health", "health check", "health findings", "health metrics",
    "health report", "history", "human", "human access", "human add",
    "human list", "inbound", "inbound audience", "inbound code", "inbound create-email", "inbound cursor",
    "inbound inboxes", "inbound list", "inbound read", "inbound search", "inbound wait", "inbox", "intake",
    "integration", "integrations", "judge", "kpi", "kpi add", "kpi archive", "kpi create", "kpi link",
    "kpi list", "kpi log", "kpi readings", "kpi restore", "kpi show", "kpi target", "kpi unlink", "kpi update",
    "learn", "lens", "lens list", "lens run", "lens runs", "lens show", "listen", "listen judge", "listening",
    "listening decide", "listening item", "listening item list", "listening item resolve", "listening runs",
    "listening save", "listening show", "listening stats", "live", "live brief", "live stats", "mail",
    "mail list", "mail read", "maintenance", "maintenance run", "maintenance runs",
    "maintenance set", "maintenance status", "market", "market apply", "market ask", "market briefing",
    "market briefing latest", "market briefing post", "market define", "market delta", "market edges",
    "market figure", "market figure add", "market find", "market import", "market page", "market refresh",
    "market report", "market research", "market resolve", "market segment", "market segment add",
    "market segment tree", "market show", "market size", "market size accept", "market size add",
    "market size list", "market size params", "market size reject", "market sources", "market spend",
    "market sweep", "market watch", "market watch add", "market watch check", "market watch list",
    "market watch stop", "mcp", "mcp stats", "meeting", "meeting approve", "meeting delete", "meeting dismiss",
    "meeting granola", "meeting granola status", "meeting granola sync", "meeting import", "meeting link", "meeting pending",
    "meeting read", "meeting restore", "meeting search", "meeting unlink", "meetings", "meetings transcript", "message",
    "message list", "message mark-read", "message redact", "message send", "needs-you", "needs-you abandon",
    "needs-you commit", "needs-you next", "needs-you respond", "needs-you show", "needs-you start", "note",
    "note create", "note delete", "note list", "notes", "notice", "notice list", "org", "org models",
    "org order", "org reports-to", "origin", "origin show", "page", "page archive", "page list", "page publish",
    "page show", "people", "person", "pin", "pin add", "pin remove", "pin update", "pipeline", "proposal",
    "proposal create", "proposal decide", "proposal list", "queries", "question", "question answer",
    "question ask", "readers", "readers list", "recent", "relay", "relay set", "relay status", "relay wake-url",
    "relay wake-url off", "relay wake-url set", "relay wake-url show", "release", "release now",
    "release schedule", "replay", "replay show", "repo", "repo list", "repo product-create", "repo review",
    "repo tick", "repo untick", "roadmap", "roadmap settings", "roadmap tasks", "rule", "rule accept",
    "rule add", "rule appeal", "rule ask", "rule check", "rule due", "rule due add", "rule due edit",
    "rule due list", "rule due satisfied", "rule edit", "rule list", "rule read", "rule retire", "rule skip",
    "rule suggested", "rule withdraw", "rules", "rules accept", "rules add", "rules appeal", "rules ask",
    "rules check", "rules due", "rules due add", "rules due edit", "rules due list", "rules due satisfied",
    "rules edit", "rules list", "rules read", "rules retire", "rules skip", "rules suggested", "rules withdraw",
    "run", "run export", "run list", "say", "seat", "seat close", "seat create", "seat list", "seat update",
    "service-key", "service-key create", "service-key list", "service-key revoke", "settings", "settings ask",
    "settings find", "settings set", "settings show", "skill", "skill copy", "slack", "slack channel",
    "slack channel add", "slack channel import", "slack channel list", "slack channel remove", "sleep",
    "sleep set", "sleep status", "sql", "status", "strategy", "strategy bet",
    "strategy bet add", "strategy bet retire", "strategy bet update", "strategy diff", "strategy draft",
    "strategy guides", "strategy publish", "strategy show", "strategy summary", "strategy versions", "support",
    "support file", "support report", "support send", "tag", "tag create", "tag list", "tag show", "tag update",
    "task", "task answers", "task ask", "task ask-resolve", "task attach", "task bulk", "task cap",
    "task cap set", "task caps", "task check-back", "task child", "task close", "task comment",
    "task comment-delete", "task comment-edit", "task comment-moderate", "task create", "task defer",
    "task delete", "task deleted", "task done", "task label", "task later", "task link", "task list",
    "task meetings", "task model", "task publication-preview", "task question", "task relate", "task requester-remove",
    "task requester-save", "task requesters", "task restore", "task run", "task show", "task snooze",
    "task stuck", "task tree", "task type", "task type create", "task type delete", "task type update",
    "task types", "task unsnooze", "task update", "task wait", "task waive-qa", "task worktree",
    "task worktree add", "task worktree attach", "task worktree setup", "task-manager", "task-manager bot",
    "task-manager off", "task-manager on", "task-manager run", "task-manager schedule", "task-manager status",
    "tasks", "tasks answers", "tasks ask", "tasks ask-resolve", "tasks attach", "tasks bulk", "tasks cap",
    "tasks cap set", "tasks caps", "tasks check-back", "tasks child", "tasks close", "tasks comment",
    "tasks comment-delete", "tasks comment-edit", "tasks comment-moderate", "tasks create", "tasks defer",
    "tasks delete", "tasks deleted", "tasks done", "tasks label", "tasks later", "tasks link", "tasks list",
    "tasks meetings", "tasks model", "tasks publication-preview", "tasks question", "tasks relate", "tasks requester-remove",
    "tasks requester-save", "tasks requesters", "tasks restore", "tasks run", "tasks show", "tasks snooze",
    "tasks tree", "tasks type", "tasks type create", "tasks type delete", "tasks type update", "tasks types",
    "tasks unsnooze", "tasks update", "tasks wait", "tasks waive-qa", "tasks worktree", "tasks worktree add",
    "tasks worktree attach", "tasks worktree setup", "team", "team budget", "team icon", "team show",
    "template", "template list", "thread", "thread bot-status", "thread catch-up", "thread catchup",
    "thread create", "thread delete", "thread dm", "thread dm-group", "thread edit", "thread follow",
    "thread home", "thread link-slack", "thread list", "thread members", "thread members add",
    "thread members list", "thread members remove", "thread open-earlier", "thread pin", "thread post",
    "thread react", "thread read", "thread reply", "thread search", "thread show", "thread slack-resume",
    "thread sub", "thread task", "thread unfollow", "thread unpin", "thread unreact", "thread update",
    "thread upload", "tool", "tool add", "tool learn", "tool list", "tool query-search", "tool remove",
    "tool report", "tool show", "tool update", "tools", "trigger", "trigger create", "trigger delete",
    "trigger deliveries", "trigger delivery", "trigger list", "trigger recover", "trigger relay",
    "trigger relay create", "trigger relay delete", "trigger relay rotate", "trigger replay", "trigger rotate",
    "trigger show", "trigger sign", "trigger update", "turns", "unnote", "update", "update create",
    "update list", "update mark-read", "update post", "update read", "update redo", "update reply",
    "update settings", "update show", "updates", "webhook", "webhook create", "webhook delete",
    "webhook enable", "webhook example", "webhook list", "webhook pause", "webhook recover", "webhook replay",
    "webhook resume", "webhook retry", "webhook rotate", "webhook send", "webhook sends", "webhook show",
    "webhook update", "whoami",
];

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
    let command = if command.contains("<<") { command.split('\n').next().unwrap_or("") } else { command };
    if let Some(words) = command_runs(command).into_iter().find_map(hub_words) {
        return hub_summary(&words);
    }
    plain_program(command).map(clip).unwrap_or_default()
}

/// The script of a line that is only `sh`, `bash`, `zsh` or `dash`, one plain `-c` flag (`-c`, `-lc`, `-ec`) and one script word.
fn shell_script(command: &str) -> Option<String> {
    let words = shell_words(command)?;
    let [shell, flag, script] = words.as_slice() else { return None };
    // The line itself reads `<shell> <flag> ` as written, unquoted, so no quoting, separator or redirection changes them.
    let blank = [' ', '\t'];
    let plain = shell.chars().all(|c| c.is_ascii_alphanumeric() || "._+/-".contains(c))
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
        && matches!(flag.as_str(), "-c" | "-lc" | "-ic" | "-ec" | "-xc" | "-lic" | "-ilc"))
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
    if plain_program(run)? != "hub" {
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
    NAME.get_or_init(|| Regex::new(r"^[A-Za-z0-9._+-]{1,40}$").unwrap()).is_match(program)
}

/// The runs a shell line holds: its text cut at `&&`, `||`, `|`, `;` and newlines. A separator inside quotes, inside
/// `$(...)` or inside backticks keeps the run whole, and a lone `&` (as in `2>&1`) is not a separator. A `#` that starts
/// a word comments out the rest of its line: the comment is in no run, and a separator inside it splits nothing.
fn command_runs(command: &str) -> Vec<&str> {
    let mut runs: Vec<&str> = vec![];
    let (mut start, mut quote, mut escaped, mut depth, mut ticked) = (0, None::<char>, false, 0usize, false);
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
    command[..i].chars().next_back().is_none_or(|p| p.is_whitespace() || ";|&()".contains(p))
}

/// `hub` and the command path its words name, read in order from the positional words before the first flag: the first
/// word only if it is a top-level command, the next only if it is a subcommand of that command, and so on to three words.
/// Reading stops at the first word that does not continue a path, so an argument, a recipient or a note never reaches the
/// summary (`hub ask finance "x"` reads `hub ask`; `hub task close 7f3a` reads `hub task close`).
fn hub_summary(words: &[String]) -> String {
    let mut path: Vec<&str> = vec![];
    for word in words[1..].iter().take(3).take_while(|w| !w.starts_with('-')) {
        path.push(word.as_str());
        if !HUB_PATHS.contains(&path.join(" ").as_str()) {
            path.pop();
            break;
        }
    }
    std::iter::once("hub").chain(path).collect::<Vec<_>>().join(" ")
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
    word.split_once('=').is_some_and(|(name, _)| !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
}

fn clip(text: &str) -> String {
    let line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= TOOL_SUMMARY_CHARS {
        line
    } else {
        format!("{}…", line.chars().take(TOOL_SUMMARY_CHARS - 1).collect::<String>())
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
    let r = unsafe { libc::waitid(libc::P_PID, pid as libc::id_t, &mut info, libc::WEXITED | libc::WNOHANG | libc::WNOWAIT) };
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
pub fn own(mut child: tokio::process::Child) -> (Stopper, tokio::sync::oneshot::Receiver<std::io::Result<std::process::ExitStatus>>) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Stop>();
    let (done, exited) = tokio::sync::oneshot::channel();
    let pid = child.id().map(|p| p as i32);
    // SAFETY: getpgid only reads; the child is not reaped yet, so the pid is still its own.
    let mut group = pid.filter(|&p| unsafe { libc::getpgid(p) } == p);
    tokio::spawn(async move {
        // A group leader's exit is watched on each SIGCHLD without reaping it (`exited_unreaped`); any other child is
        // reaped by its wait directly. Without the signal stream, a group leader is too, and nothing follows its exit.
        let mut exits = group.and_then(|_| tokio::signal::unix::signal(tokio::signal::unix::SignalKind::child()).ok());
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

    pub async fn resume_thread(&self, thread_id: &str, settings: &Settings) -> Result<String, HostError> {
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

    pub async fn start_turn(&self, thread_id: &str, text: &str, effort: Option<&str>) -> Result<String, HostError> {
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
            Host::MuseCode(_) => Err(HostError::SteerRefused("Muse Code cannot steer a running turn".into())),
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
        matches!(self, Host::Claude(_) | Host::Codex(_) | Host::Gemini(_) | Host::Pi(_))
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
        crate::harness_tools::caps(runtime, harness).compaction_events && matches!(self, Host::Claude(_) | Host::Pi(_))
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
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(harness)
    }

    /// (fixture path, the events the Python host makes of it), for every fixture of `harness`.
    pub fn cases(harness: &str) -> Vec<(PathBuf, Vec<Value>)> {
        let mut out = vec![];
        let mut paths: Vec<PathBuf> = std::fs::read_dir(dir(harness)).unwrap().map(|e| e.unwrap().path()).collect();
        paths.sort();
        for p in paths.into_iter().filter(|p| p.extension().is_some_and(|e| e == "jsonl")) {
            let expected: Value = serde_json::from_str(&std::fs::read_to_string(p.with_extension("expected.json")).unwrap()).unwrap();
            out.push((p, expected.as_array().unwrap().clone()));
        }
        assert!(!out.is_empty(), "no {harness} fixtures");
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A harness in its own group that starts a child; its pid (the group) and the child's.
    async fn grouped_harness() -> (tokio::process::Child, i32, i32) {
        use tokio::io::AsyncBufReadExt;
        let mut command = tokio::process::Command::new("/bin/sh");
        command.args(["-c", "sleep 900 </dev/null >/dev/null 2>&1 & echo $!; while :; do sleep 1; done"]);
        command.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped());
        let mut child = new_group(&mut command).spawn().unwrap();
        let mut lines = tokio::io::BufReader::new(child.stdout.take().unwrap()).lines();
        let tool: i32 = lines.next_line().await.unwrap().unwrap().trim().parse().unwrap();
        let group = child.id().unwrap() as i32;
        (child, group, tool)
    }

    fn group_gone(group: i32) -> bool {
        // SAFETY: signal 0 only checks whether any process of the group is left.
        unsafe { libc::kill(-group, 0) == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) }
    }

    #[tokio::test]
    async fn a_terminate_ends_the_whole_group_and_a_kill_ends_only_the_leader() {
        let (child, group, tool) = grouped_harness().await;
        // SAFETY: getpgid only reads.
        assert_eq!(unsafe { libc::getpgid(tool) }, group);
        let (stopper, exited) = own(child);
        stopper.terminate(std::time::Duration::from_secs(5));
        tokio::time::timeout(std::time::Duration::from_secs(5), exited).await.unwrap().unwrap().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while !group_gone(group) && std::time::Instant::now() < deadline {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        assert!(group_gone(group), "the harness's child outlived the stop");

        // A background-wait end kills the harness alone: the shells it keeps run on in its group.
        let (child, group, tool) = grouped_harness().await;
        let (stopper, exited) = own(child);
        stopper.kill();
        tokio::time::timeout(std::time::Duration::from_secs(5), exited).await.unwrap().unwrap().unwrap();
        // SAFETY: signal 0 only checks the pid.
        let kept = unsafe { libc::kill(tool, 0) } == 0;
        // SAFETY: the child this test started, and its group.
        unsafe { libc::kill(-group, libc::SIGKILL) };
        assert!(kept, "a kill reached the harness's child");
    }

    #[test]
    fn a_tool_summary_names_the_file_or_program_but_never_the_arguments() {
        assert_eq!(tool_summary(&json!({"command": "curl -H 'Authorization: Bearer sk-live-1' https://api.example.com"})), "curl");
        assert_eq!(tool_summary(&json!({"command": "TOKEN=sk-live-1 /usr/bin/python3 run.py --key sk-live-1"})), "python3");
        assert_eq!(command_program("/bin/bash -lc 'gh api -H \"Authorization: token x1\" /user'"), "gh");
        assert_eq!(tool_summary(&json!({"command": "TOKEN=\"Bearer sk-live-abc\" curl https://api.example.com"})), "curl");
        assert_eq!(tool_summary(&json!({"command": "AUTH='x sk-live-abc' ./run.sh"})), "run.sh");
        assert_eq!(tool_summary(&json!({"command": "bash -c \"TOKEN='a sk-live-abc' curl x\""})), "curl");
        assert_eq!(tool_summary(&json!({"command": "TOKEN='sk-live-abc curl"})), "");
        assert_eq!(tool_summary(&json!({"command": "TOKEN=$(echo sk-live-abc) curl x"})), "");
        assert_eq!(tool_summary(&json!({"command": "TOKEN=`echo sk-live-abc` curl x"})), "");
        assert_eq!(tool_summary(&json!({"command": "TOKEN=${T:-a sk-live-abc} curl x"})), "");
        assert_eq!(tool_summary(&json!({"command": "curl -H \"X: $(cat key)\" x"})), "curl");
        assert_eq!(tool_summary(&json!({"file_path": "/work/note.txt", "content": "secret body"})), "/work/note.txt");
        assert_eq!(tool_summary(&json!({"description": "count words", "prompt": "Count the words", "subagent_type": "general-purpose"})), "");
        assert_eq!(tool_summary(&json!({"query": "private question", "url": "https://x.example/?token=1"})), "");
    }

    #[test]
    fn a_compound_command_names_the_work_and_never_the_arguments() {
        assert_eq!(command_program("cd /repo && hub task close --id 42 --reason 'sk-live-1'"), "hub task close");
        assert_eq!(command_program("export HUB_TOKEN=sk-live-1; /usr/local/bin/hub task create --title sk-live-1"), "hub task create");
        assert_eq!(command_program("hub task note sk-live-1 close"), "hub task");
        assert_eq!(command_program("cd web && npm run build && hub task list"), "hub task list");
        assert_eq!(command_program("set -e\nnpm test | tee out.log"), "set");
        assert_eq!(command_program("cargo test 2>&1 | tail"), "cargo");
        assert_eq!(command_program("cd /tmp"), "cd");
        assert_eq!(command_program("(cd web && npm test)"), "");
        assert_eq!(command_program("sh -c 'cd x && cargo test'"), "cd");
        assert_eq!(command_program("hub task create --title \"a && hub task close\""), "hub task create");
        assert_eq!(command_program("hub ask botops please check the release"), "hub ask");
        assert_eq!(command_program("FOO=1 && curl x"), "");
        assert_eq!(command_program("X=$(a && sk-live-1) hub task list"), "");
        assert_eq!(tool_summary(&json!({"command": "cd web && hub task list --token sk-live-1"})), "hub task list");
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
            assert!(!shown.contains("private") && !shown.contains("customer"), "{line:?} showed {shown:?}");
        }
        assert_eq!(command_program("cd /repo <<'EOF'\nprivate-token\nEOF\nnpm test"), "cd");
        assert_eq!(command_program("cd /repo && >/tmp/customer-123.json printf ok"), "cd");
        assert_eq!(command_program(">|private-token printf ok"), "");
        assert_eq!(command_program("cd /tmp && >|private-token printf ok"), "cd");
        assert_eq!(command_program("(( 1 || private_token ))"), "");
        assert_eq!(command_program("cd /w && hub task close 7f3a"), "hub task close");
        assert_eq!(command_program("cd /w && npm test"), "cd");
    }

    #[test]
    fn a_shell_comment_is_in_no_run() {
        assert_eq!(command_program("cd /repo # note; private-token\nnpm test"), "cd");
        assert_eq!(command_program("cd /repo # note; private-token"), "cd");
        assert_eq!(command_program("echo ok # x && hub task close"), "echo");
        assert_eq!(command_program("cd x; a#b && hub task list"), "hub task list");
        assert_eq!(command_program("echo '# x'; hub task list"), "hub task list");
        assert_eq!(command_program("sk-live-1$x y"), "");
    }

    #[test]
    fn a_hub_summary_stops_at_the_first_word_that_does_not_continue_a_command_path() {
        assert_eq!(command_program("hub ask finance \"private question\""), "hub ask");
        assert_eq!(command_program("hub history review"), "hub history");
        assert_eq!(command_program("hub task close 7f3a"), "hub task close");
        assert_eq!(command_program("hub needs-you show"), "hub needs-you show");
        assert_eq!(command_program("hub bot status history"), "hub bot status history");
        assert_eq!(command_program("hub nosuch command"), "hub");
    }

    #[test]
    fn a_hub_line_inside_a_heredoc_body_is_text() {
        assert_eq!(command_program("cat <<'EOF'\nhub approval approve\nEOF"), "cat");
        assert_eq!(command_program("cat > /tmp/x <<EOF && echo ok\nhub task close 7f3a\nEOF"), "cat");
        assert_eq!(command_program("hub automation create --file - <<'EOF'\n{\"name\": \"x\"}\nEOF"), "hub automation create");
        assert_eq!(command_program("cd /w && hub automation create --file - <<'EOF'\n{\"name\": \"x\"}\nEOF"), "hub automation create");
        assert_eq!(command_program("<<'EOF'\nhub approval approve\nEOF"), "");
        assert_eq!(command_program("<<EOF\ncustomer-private\nEOF"), "");
        assert_eq!(command_program(">'>' printf customer-private"), "");
        assert_eq!(command_program("cd /w && >\\> printf customer-private"), "cd");
        assert_eq!(command_program("cd /w && >'>' hub task close customer-private"), "cd");
        assert_eq!(command_program("TOKEN=customer-private >'>' printf customer-private"), "");
        assert_eq!(command_program("2> >(printf customer-private) true"), "");
        assert_eq!(command_program("/bin/zsh -lc 'cd /w && hub needs-you show'"), "hub needs-you show");
        assert_eq!(command_program("bash --norc /tmp/customer-private.sh"), "bash");
        assert_eq!(command_program("bash -c-x 'customer-private'"), "bash");
        assert_eq!(command_program("bash -oc /tmp/customer-private.sh"), "bash");
        assert_eq!(command_program("bash;<<\\'\n-c\n'customer-private\n'"), "bash");
        assert_eq!(command_program("bash; -c /tmp/customer-private.json"), "bash");
        assert_eq!(command_program("bash \"-\\c\" /tmp/customer-private.json"), "bash");
        assert_eq!(command_program("bash \n-c /tmp/customer-private.json"), "bash");
        assert_eq!(command_program("NOTE=\rcustomer-private"), "");
        assert_eq!(command_program("NOTE=\u{a0}customer-private"), "");
        assert_eq!(command_program("bash -c \"NOTE=\\ customer-private /usr/bin/true\""), "");
        assert_eq!(command_program("bash -c \"NOTE=\\$x customer-private\""), "");
        assert_eq!(command_program("bash -c \r customer-private"), "bash");
        assert_eq!(command_program("bash -c \u{a0} customer-private"), "bash");
        assert_eq!(
            command_program("bash -c \"NOTE='$(printf %b \\\\047\\\\040/usr/bin/true\\\\040\\\\047)' customer-private\""),
            "bash"
        );
        assert_eq!(command_program("bash -c \"NOTE='`printf x`' customer-private\""), "bash");
        assert_eq!(command_program("bash -lc 'cd /w && hub needs-you show $X'"), "hub needs-you show");
        assert_eq!(command_program("bash -c \"cd /w && hub \\\nneeds-you show\""), "hub needs-you show");
    }

    #[test]
    fn a_heredoc_substitution_or_redirection_never_shows_its_arguments() {
        // Nested quotes inside a substitution: the line is one run and reads no further than its first program.
        let nested = command_program(r#"export X="$(printf '%s' "a; private-token")""#);
        assert!(nested.is_empty() || nested == "export", "{nested}");
        assert!(!nested.contains("private-token"));
        // A heredoc body is no run.
        assert_eq!(command_program("cd /repo <<'EOF'\nprivate-token\nEOF\nnpm test"), "cd");
        // A redirection is skipped to the program behind it, whether its target is attached or the next word.
        assert_eq!(command_program("cd /repo && >/tmp/customer-123.json printf ok"), "cd");
        assert_eq!(command_program("cd /repo && > /tmp/customer-123.json printf ok"), "cd");
        assert_eq!(command_program("cd /w && 2>&1 npm test"), "cd");
        // A program word is a plain path, and a program name is at most 40 characters.
        assert_eq!(command_program("$HOME/bin/tool --x"), "");
        assert_eq!(command_program(&format!("{} x", "a".repeat(41))), "");
        // A hub command in a later run still reads; any other line reads as its first program.
        assert_eq!(command_program("cd /w && hub task close 7f3a"), "hub task close");
        assert_eq!(command_program("cd /w && npm test"), "cd");
    }

    #[test]
    fn a_new_message_starts_a_paragraph_and_pieces_of_one_message_join_with_nothing() {
        let mut reply = String::new();
        assert_eq!(add_piece(&mut reply, true, "Holding until "), "Holding until ");
        assert_eq!(add_piece(&mut reply, false, "it recovers. "), "it recovers. ");
        assert_eq!(add_piece(&mut reply, true, "No change."), "\n\nNo change.");
        assert_eq!(reply, "Holding until it recovers.\n\nNo change.");
    }

    #[test]
    fn the_python_classifiers_agree() {
        assert!(is_limit("You've hit your usage limit"));
        assert!(is_limit("You've hit your session limit · resets 12:40am (UTC)"));
        assert!(is_limit("HTTP 503 overloaded"));
        assert!(!is_limit("all good"));
        assert!(is_limit("Claude AI monthly spend limit reached. Raise it in your account settings."));
        assert!(is_limit("API error 429: Subscription quota exhausted. Your usage window resets at 2026-10-19T00:00:00Z. (rate_limit_error)"));
        assert!(!is_limit("Draft the monthly spend report"));
        // An API key's quota or billing refusal, as Codex and the OpenAI API word it.
        assert!(is_limit("Quota exceeded. Check your plan and billing details."));
        assert!(is_limit("You exceeded your current quota, please check your plan and billing details. (insufficient_quota)"));
        assert!(is_limit(r#"{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}"#));
        assert!(!is_limit("Raise the storage quota for the team drive"));
        assert!(!is_limit("Quota: 40 of 100 seats used"));
        assert!(!is_limit("write /tmp/out: Disk quota exceeded (os error 122)"));
        // Capacity is a limit (a fallback may take the turn) but not quota; the plan's quota stays quota.
        assert!(is_limit("Selected model is at capacity. Please try a different model.") && is_capacity("Selected model is at capacity. Please try a different model."));
        assert!(is_capacity("HTTP 503 overloaded"));
        // An API's structured refusal ("overloaded_error"): the underscore is a word character, so it needs its own match.
        let structured = r#"API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Server busy"}}"#;
        assert!(is_limit(structured) && is_capacity(structured));
        assert!(!is_capacity("Quota exceeded. Check your plan and billing details."));
        assert!(!is_capacity("API error 429: Subscription quota exhausted. Your usage window resets at 2026-10-19T00:00:00Z. (rate_limit_error)"));
        assert!(!is_capacity("You've hit your usage limit"));
        assert!(is_auth_retryable("OAuth: failed to refresh OAuth token"));
        assert!(!is_auth_retryable("unexpected status 401 Unauthorized: Invalid API key"));
        assert!(is_auth_rejected("Invalid API key · Please run /login"));
        assert_eq!(rejection_reason("x\nInvalid API key sk-ant-abcdefghijk"), "Invalid API key [redacted]");
        assert_eq!(compact_at(&json!("120000")), 120000);
        assert_eq!(compact_at(&json!(0)), DEFAULT_COMPACT_AT);
    }

    #[test]
    fn a_bot_key_problem_is_its_own_grant_and_only_for_a_bot_that_needs_one() {
        let grants: Map<String, Value> = json!({"has": ["OPENROUTER_API_KEY"]}).as_object().cloned().unwrap();
        let pi = |model: &str| json!({"runtime": "pi", "harness": "pi", "model": model});
        assert_eq!(bot_key_problem("pi", &pi("kimi-k3"), "has", &grants), None);
        assert_eq!(bot_key_problem("pi", &pi("kimi-k3"), "none", &grants), Some("OpenRouter API key required".into()));
        // A Tico model signs in with the company's gateway key, so it needs no grant of its own.
        assert_eq!(bot_key_problem("pi", &pi("tico-mid-1.0"), "none", &grants), None);
        let gemini = json!({"runtime": "gemini", "harness": "gemini", "model": "gemini-3.7-flash"});
        assert_eq!(bot_key_problem("gemini", &gemini, "has", &grants), Some("Gemini API key required".into()));
        // Antigravity runs on the Gemini runtime with its own sign-in, not a key here.
        let antigravity = json!({"runtime": "gemini", "harness": "antigravity", "model": "gemini-3.7-flash"});
        assert_eq!(bot_key_problem("gemini", &antigravity, "none", &grants), None);
        assert_eq!(bot_key_problem("claude", &json!({"runtime": "claude"}), "none", &grants), None);
    }
}

// Per-host answers behind a capability: generic code reads the manifest's capability and asks here, so the adapters
// stay the one place that knows a CLI by name.

/// Native goals and built-in commands of one installed harness (goals.capabilities): Claude Code by version, Codex by
/// the protocol its app-server describes; every other host has none.
pub async fn probe_goals(runtime: &str, exe: &std::path::Path, version: &str) -> (bool, Vec<Value>) {
    let (mut supported, mut commands) = (false, vec![]);
    match runtime {
        "claude" => {
            supported = crate::hosts::claude::version_tuple(version).is_some_and(|v| v >= (2, 1, 212));
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
            let dir = std::env::temp_dir().join(format!("tico-goal-schema-{}", uuid::Uuid::new_v4().simple()));
            if std::fs::create_dir_all(&dir).is_ok() {
                let mut cmd = tokio::process::Command::new(exe);
                cmd.args(["app-server", "generate-json-schema", "--experimental", "--out"]).arg(&dir);
                cmd.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).kill_on_drop(true);
                if let Ok(Ok(status)) = tokio::time::timeout(std::time::Duration::from_secs(10), cmd.status()).await {
                    if status.success() {
                        let schema = dir.join("v2");
                        supported = schema.join("ThreadGoalSetParams.json").exists();
                        if schema.join("ThreadCompactStartParams.json").exists() {
                            commands.push(crate::goals::command("compact", "Compact this conversation", ""));
                        }
                        if schema.join("ReviewStartParams.json").exists() {
                            commands.push(crate::goals::command("review", "Review changes", "[instructions]"));
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
pub fn bot_key_problem(runtime: &str, config: &Value, bot: &str, grants: &Map<String, Value>) -> Option<String> {
    let (key, label) = match runtime {
        "gemini" => (gemini::uses_gemini_cli(config).then_some("GEMINI_API_KEY")?, "Gemini"),
        "pi" => (pi::needs_grant(config).then_some("OPENROUTER_API_KEY")?, "OpenRouter"),
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
pub fn use_own_config_home(runtime: &str, profile: Option<&crate::profiles::Profile>, env: &mut Env) -> bool {
    match runtime {
        "musecode" => {
            env.remove("MUSE_AUTH_PATH");
            env.insert("XDG_CONFIG_HOME".into(), musecode::config_home(profile).display().to_string());
            env.insert("XDG_DATA_HOME".into(), musecode::data_home(profile).display().to_string());
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
        "claude" => crate::readiness::CLAUDE_MODELS.iter().map(|s| s.to_string()).collect(),
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
