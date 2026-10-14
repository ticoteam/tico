//! Native harness goals and commands (runner/goals.py): which goals and slash commands each installed harness
//! offers, a bot repository's own Claude Code skills and commands, and the goal event vocabulary both hosts and
//! the drive loop share.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::state::State;
use crate::util::text_of;

/// The server keeps at most this many commands per bot (backend/models.py, GoalReadiness).
pub const MAX_COMMANDS: usize = 30;
/// A goal sent back to work this many times with no person writing in between is stuck: the runner pauses it.
pub const MAX_GOAL_TRIES: u32 = 30;
pub const MAX_CODEX_GOAL_TRIES: u32 = 100;
/// How long a pause waits for the interrupted turn to end before clearing the goal anyway (service.py).
pub const GOAL_PAUSE_WAIT_S: f64 = 30.0;
pub const WAITING_CLAUSE: &str = "Or, if the only work left is waiting on someone or something else (an answer, a review or merge, another bot, a deploy, a set time), the goal also holds for now when the last message says \"Waiting on: <what>\". Don't poll for it: a reply, a task answer, a watcher or a routine wakes the bot.";
const OLD_WAITING_CLAUSES: &[&str] = &["Also done for now: the only work left is waiting on someone or something else (an answer, a review or merge, another bot, a deploy, a set time) and the last message says \"Waiting on: <what>\". Don't poll for it: a reply, a task answer, a watcher or a routine wakes the bot."];
pub const NATIVE_GOAL_LIMIT: usize = 4000;

pub fn command(name: &str, help: &str, args: &str) -> Value {
    json!({"name": name, "args": args, "help": help, "kind": "harness"})
}

fn waiting_line() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r#"(?i)(\\?")?waiting on:[*_\s]*([^\n]+)"#).unwrap())
}

fn repo_command_name() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[a-z0-9][a-z0-9_:-]{0,63}$").unwrap())
}

type Fields = HashMap<String, String>;

fn frontmatter_cache() -> &'static Mutex<HashMap<(PathBuf, i128, u64), Fields>> {
    static C: OnceLock<Mutex<HashMap<(PathBuf, i128, u64), Fields>>> = OnceLock::new();
    C.get_or_init(Default::default)
}

/// The leading `---` block's plain `key: value` fields; folded and literal values become one line.
pub fn frontmatter(path: &Path) -> Fields {
    use std::io::Read;
    use std::os::unix::fs::MetadataExt;
    let Ok(meta) = std::fs::metadata(path) else { return Fields::new() };
    let key = (path.to_path_buf(), meta.mtime() as i128 * 1_000_000_000 + meta.mtime_nsec() as i128, meta.size());
    if let Some(f) = frontmatter_cache().lock().unwrap().get(&key) {
        return f.clone();
    }
    let mut head = vec![0u8; 8192];
    let Ok(n) = std::fs::File::open(path).and_then(|mut f| f.read(&mut head)) else { return Fields::new() };
    let head = String::from_utf8_lossy(&head[..n]).into_owned();
    let mut fields = Fields::new();
    let lines: Vec<&str> = head.lines().collect();
    if lines.first().is_some_and(|l| l.trim() == "---") {
        static KV: OnceLock<Regex> = OnceLock::new();
        let kv = KV.get_or_init(|| Regex::new(r"^([A-Za-z][\w-]*):\s*(.*)$").unwrap());
        let mut name: Option<String> = None;
        for line in &lines[1..] {
            if line.trim() == "---" {
                break;
            }
            if let Some(c) = kv.captures(line) {
                let (k, v) = (c[1].to_lowercase(), c[2].trim().to_string());
                let v = if [">", "|", ">-", "|-"].contains(&v.as_str()) { String::new() } else { v.trim_matches(|c| c == '\'' || c == '"').to_string() };
                fields.insert(k.clone(), v);
                name = Some(k);
            } else if let Some(k) = name.as_ref().filter(|_| line.starts_with([' ', '\t'])) {
                let joined = format!("{} {}", fields[k], line.trim()).trim().to_string();
                fields.insert(k.clone(), joined);
            }
        }
    }
    let mut cache = frontmatter_cache().lock().unwrap();
    if cache.len() > 2000 {
        cache.clear();
    }
    cache.insert(key, fields.clone());
    fields
}

/// A Claude Code bot's own skills and commands, from its repository's .claude folder.
pub fn repo_commands(root: &Path) -> Vec<Value> {
    let mut found: Vec<(String, Fields)> = vec![];
    let sorted = |dir: &Path| -> Vec<PathBuf> {
        let mut v: Vec<PathBuf> = std::fs::read_dir(dir).map(|r| r.filter_map(|e| e.ok().map(|e| e.path())).collect()).unwrap_or_default();
        v.sort();
        v
    };
    let skills = root.join(".claude").join("skills");
    for folder in sorted(&skills).into_iter().filter(|p| p.is_dir()).take(MAX_COMMANDS * 2) {
        let fields = frontmatter(&folder.join("SKILL.md"));
        if !fields.is_empty() && fields.get("user-invocable").map(|v| v.to_lowercase()) != Some("false".into()) {
            let name = fields.get("name").filter(|n| !n.is_empty()).cloned().unwrap_or_else(|| folder.file_name().unwrap().to_string_lossy().into());
            found.push((name, fields));
        }
    }
    let commands = root.join(".claude").join("commands");
    if commands.is_dir() {
        let md = |p: &PathBuf| p.extension().is_some_and(|e| e == "md");
        let mut files: Vec<PathBuf> = sorted(&commands).into_iter().filter(md).collect();
        let mut nested: Vec<PathBuf> = sorted(&commands).into_iter().filter(|p| p.is_dir()).flat_map(|d| sorted(&d)).filter(md).collect();
        nested.sort();
        files.extend(nested);
        for path in files.into_iter().take(MAX_COMMANDS * 2) {
            let fields = frontmatter(&path);
            if path.is_file() {
                found.push((path.file_stem().unwrap().to_string_lossy().into(), fields));
            }
        }
    }
    let mut result = vec![];
    for (name, fields) in found {
        let name = name.trim().to_lowercase();
        if !repo_command_name().is_match(&name) {
            continue;
        }
        let help = fields.get("description").map(|d| d.split_whitespace().collect::<Vec<_>>().join(" ")).unwrap_or_default();
        let help = if help.chars().count() <= 80 { help } else { format!("{}…", help.chars().take(79).collect::<String>().trim_end()) };
        let args: String = fields.get("argument-hint").cloned().unwrap_or_default().chars().take(100).collect();
        result.push(command(&name, &help, &args));
    }
    result
}

/// The harness's own commands first, then the repository's, without repeating a name.
pub fn with_repo_commands(builtins: &[Value], root: &Path) -> Vec<Value> {
    let mut merged = builtins.to_vec();
    let mut seen: std::collections::HashSet<String> = builtins.iter().map(|b| text_of(&b["name"])).collect();
    for item in repo_commands(root) {
        let name = text_of(&item["name"]);
        if !seen.contains(&name) && merged.len() < MAX_COMMANDS {
            seen.insert(name);
            merged.push(item);
        }
    }
    merged
}

fn capability_cache() -> &'static Mutex<HashMap<(String, String, String), Map<String, Value>>> {
    static C: OnceLock<Mutex<HashMap<(String, String, String), Map<String, Value>>>> = OnceLock::new();
    C.get_or_init(Default::default)
}

/// {goals, commands} for one installed harness (goals.capabilities): Claude Code by version, Codex by the
/// protocol its app-server describes. Kept per (runtime, executable, version).
pub async fn capabilities(runtime: &str, executable: Option<&Path>, version: &str) -> Map<String, Value> {
    let Some(exe) = executable else {
        let mut m = Map::new();
        m.insert("goals".into(), json!(false));
        m.insert("commands".into(), json!([]));
        return m;
    };
    let key = (runtime.to_string(), exe.display().to_string(), version.to_string());
    if let Some(m) = capability_cache().lock().unwrap().get(&key) {
        return m.clone();
    }
    let (mut supported, mut commands) = (false, vec![]);
    if runtime == "claude" {
        supported = crate::hosts::claude::version_tuple(version).is_some_and(|v| v >= (2, 1, 212));
        if supported {
            commands = vec![
                command("compact", "Compact this conversation", "[instructions]"),
                command("clear", "Clear the harness conversation", ""),
                command("model", "Change the harness model", "[model]"),
            ];
        }
    } else if runtime == "codex" {
        // The installed protocol, rather than a version guess, determines native goal support.
        let dir = std::env::temp_dir().join(format!("tico-goal-schema-{}", uuid::Uuid::new_v4().simple()));
        if std::fs::create_dir_all(&dir).is_ok() {
            let mut cmd = tokio::process::Command::new(exe);
            cmd.args(["app-server", "generate-json-schema", "--experimental", "--out"]).arg(&dir);
            cmd.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).kill_on_drop(true);
            if let Ok(Ok(status)) = tokio::time::timeout(Duration::from_secs(10), cmd.status()).await {
                if status.success() {
                    let schema = dir.join("v2");
                    supported = schema.join("ThreadGoalSetParams.json").exists();
                    if schema.join("ThreadCompactStartParams.json").exists() {
                        commands.push(command("compact", "Compact this conversation", ""));
                    }
                    if schema.join("ReviewStartParams.json").exists() {
                        commands.push(command("review", "Review changes", "[instructions]"));
                    }
                }
            }
            let _ = std::fs::remove_dir_all(&dir);
        }
    }
    let mut m = Map::new();
    m.insert("goals".into(), json!(supported));
    m.insert("commands".into(), json!(commands));
    let mut cache = capability_cache().lock().unwrap();
    if cache.len() >= 32 {
        cache.clear();
    }
    cache.insert(key, m.clone());
    m
}

/// What the harness is given: the person's objective and the waiting clause, when both fit.
pub fn native_objective(objective: &str) -> String {
    let full = format!("{} {WAITING_CLAUSE}", objective.trim_end());
    if full.chars().count() <= NATIVE_GOAL_LIMIT { full } else { objective.to_string() }
}

fn revision_of(goal: &Value) -> String {
    format!("{}@{}", text_of(&goal["id"]), text_of(&goal["updated_at"]))
}

/// Whether this task turn must set its task's goal in the session: the server handed it one this thread has not
/// been given at this revision.
pub fn task_goal_pending(attempt: &Value, state: &State, thread: &str) -> bool {
    let goal = &attempt["chat_goal"];
    if goal["status"] != "active" || text_of(&goal["id"]).is_empty() || attempt["task"]["goal"]["goal_id"] != goal["id"] {
        return false;
    }
    state.repo_rules(&format!("goal:{thread}"), None).ok().flatten() != Some(revision_of(goal))
}

/// Record that `thread` holds this goal revision, so later turns on the task continue it.
pub fn task_goal_set(state: &State, thread: &str, goal: &Value) {
    let _ = state.repo_rules(&format!("goal:{thread}"), Some(&revision_of(goal)));
}

/// The person's objective back from what the harness reports.
pub fn tico_objective(text: &Value) -> Value {
    if let Some(t) = text.as_str() {
        for clause in std::iter::once(WAITING_CLAUSE).chain(OLD_WAITING_CLAUSES.iter().copied()) {
            if let Some(head) = t.strip_suffix(clause) {
                return json!(head.trim_end());
            }
        }
    }
    text.clone()
}

/// The bot's "Waiting on: <what>" line, from its last message or the judge's reason, first found.
pub fn waiting_on(texts: &[&str]) -> String {
    for text in texts {
        for c in waiting_line().captures_iter(text) {
            let mut what = c[2].replace("\\\"", "\"");
            if c.get(1).is_some() && what.contains('"') {
                what = what.rsplit_once('"').map(|(a, _)| a.to_string()).unwrap_or(what);
            }
            let what = what.trim().trim_end_matches('\\').trim_matches(|c| c == ' ' || c == '*' || c == '_' || c == '\'');
            if !what.is_empty() && !what.starts_with('<') {
                return format!("Waiting on: {}", what.chars().take(300).collect::<String>());
            }
        }
    }
    String::new()
}

pub fn codex_status(goal: &Value) -> String {
    let status = text_of(&goal["status"]);
    match status.as_str() {
        "complete" | "completed" => "met".into(),
        "blocked" => "paused".into(),
        "usageLimited" | "budgetLimited" | "budget_limited" => "stopped".into(),
        _ => status,
    }
}

/// A Claude stream or transcript line's goal status as a goal event's fields, or None.
pub fn claude_status(message: &Value) -> Option<Map<String, Value>> {
    let attachment = if message["attachment"].is_object() { &message["attachment"] } else { message };
    if attachment["type"] != "goal_status" {
        return None;
    }
    let t = |k: &str| crate::util::truthy(attachment.get(k));
    let mut status = if t("failed") { "stopped" } else if t("met") { "met" } else if t("sentinel") { "cleared" } else { "active" };
    // A false sentinel marks goal creation; a true sentinel marks an explicit clear.
    if t("sentinel") {
        status = if t("met") { "cleared" } else { "active" };
    }
    let mut event = Map::new();
    event.insert("status".into(), json!(status));
    event.insert("note".into(), json!(text_of(&attachment["reason"])));
    event.insert("objective".into(), attachment.get("condition").cloned().unwrap_or(Value::Null));
    if status == "active" && attachment.get("sentinel").is_none() {
        // The judge sent the bot back to work: one try. The record's id tells a re-read line from a new one.
        event.insert("tried".into(), json!(true));
        event.insert("record".into(), message.get("uuid").cloned().unwrap_or(Value::Null));
    }
    Some(event)
}

/// Goal continuations counted per (bot, session key) thread (service.py goal_tries / goal_tried).
#[derive(Default)]
pub struct Tries(Mutex<HashMap<(String, String), (u32, String)>>);

impl Tries {
    /// Count one continuation; true once it has tried `cap` times with no person in between.
    pub fn tried(&self, key: &(String, String), note: &str, cap: u32) -> bool {
        let mut map = self.0.lock().unwrap();
        let row = map.entry(key.clone()).or_insert((0, String::new()));
        row.0 += 1;
        if !note.trim().is_empty() {
            row.1 = note.trim().to_string();
        }
        row.0 >= cap
    }

    pub fn take(&self, key: &(String, String)) -> Option<(u32, String)> {
        self.0.lock().unwrap().remove(key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waiting_lines_and_objectives_read_as_python_does() {
        assert_eq!(waiting_on(&["done.\n\nWaiting on: **Ana's review**", ""]), "Waiting on: Ana's review");
        assert_eq!(waiting_on(&["", r#"the bot said \"Waiting on: the deploy\" so"#]), "Waiting on: the deploy");
        assert_eq!(waiting_on(&["Waiting on: <what>"]), "");
        let native = native_objective("Ship it");
        assert!(native.ends_with(WAITING_CLAUSE));
        assert_eq!(tico_objective(&json!(native)), json!("Ship it"));
        assert_eq!(native_objective(&"x".repeat(3990)), "x".repeat(3990));
        assert_eq!(codex_status(&json!({"status": "complete"})), "met");
        assert_eq!(codex_status(&json!({"status": "blocked"})), "paused");
    }

    #[test]
    fn claude_goal_records_become_goal_events() {
        let tried = claude_status(&json!({"uuid": "u1", "attachment": {"type": "goal_status", "condition": "c", "reason": "not yet"}})).unwrap();
        assert_eq!(Value::Object(tried), json!({"status": "active", "note": "not yet", "objective": "c", "tried": true, "record": "u1"}));
        let set = claude_status(&json!({"type": "goal_status", "sentinel": false, "condition": "c"})).unwrap();
        assert_eq!(set["status"], "active");
        assert!(!set.contains_key("tried"));
        assert_eq!(claude_status(&json!({"type": "goal_status", "sentinel": true, "met": true})).unwrap()["status"], "cleared");
        assert_eq!(claude_status(&json!({"type": "goal_status", "met": true})).unwrap()["status"], "met");
        assert!(claude_status(&json!({"type": "assistant"})).is_none());
    }

    #[test]
    fn repository_skills_and_commands_follow_the_builtins() {
        let root = std::env::temp_dir().join(format!("tico-goals-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(root.join(".claude/skills/deploy")).unwrap();
        std::fs::create_dir_all(root.join(".claude/skills/hidden")).unwrap();
        std::fs::create_dir_all(root.join(".claude/commands/ops")).unwrap();
        std::fs::write(root.join(".claude/skills/deploy/SKILL.md"), "---\nname: deploy\ndescription: >\n  Ship the\n  site\nargument-hint: [env]\n---\n").unwrap();
        std::fs::write(root.join(".claude/skills/hidden/SKILL.md"), "---\nuser-invocable: false\n---\n").unwrap();
        std::fs::write(root.join(".claude/commands/compact.md"), "x").unwrap();
        std::fs::write(root.join(".claude/commands/ops/Restart.md"), "---\ndescription: Restart it\n---\n").unwrap();
        let builtins = vec![command("compact", "Compact this conversation", "[instructions]")];
        let merged = with_repo_commands(&builtins, &root);
        let names: Vec<String> = merged.iter().map(|c| text_of(&c["name"])).collect();
        assert_eq!(names, ["compact", "deploy", "restart"]);
        assert_eq!(merged[1]["help"], "Ship the site");
        assert_eq!(merged[1]["args"], "[env]");
        std::fs::remove_dir_all(root).ok();
    }
}
