//! What a bot's own bot.yaml adds to the runner (service.py around `preflight`, `maintain` and `execute`): its
//! declared tools and mail request in readiness, its MCP servers in each run, its watchers, its memory history.
//! One field on the Runner, so the run loop and a turn reach these with one-line hooks.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use serde_json::{Map, Value};

use crate::config::Config;
use crate::env::Env;
use crate::util::text_of;

#[derive(Default)]
pub struct PerBot {
    pub watchers: Arc<crate::watchers::Watchers>,
    pub reach: crate::mcp::Reach,
    memory_at: Mutex<HashMap<String, Instant>>,
}

impl PerBot {
    /// Readiness rows gain what bot.yaml declares: `mail_request`, `tools` (names, never values) with each MCP
    /// server's reachability, and a warning per MCP server the bot's harness cannot take.
    pub fn decorate(
        &self,
        config: &Config,
        checks: &mut [Map<String, Value>],
        candidates: &[Value],
    ) {
        for (row, entry) in checks.iter_mut().zip(candidates) {
            let bot = text_of(&row["bot"]);
            if row["repository_present"] != true {
                continue;
            }
            let path = crate::sessions::local_path(config, &bot, &entry["config"]);
            let Some(declared) = crate::manifest::read(&path).filter(|d| d.is_object()) else {
                continue;
            };
            if let Some(request) = crate::declared_access::mail_request(&declared) {
                row.insert("mail_request".into(), request);
            }
            let access = crate::manifest::tools_of(&declared);
            if !crate::util::truthy(Some(&access)) {
                continue;
            }
            let held = crate::readiness_extra::held(&bot);
            let mut tools = crate::declared_access::declared_tools(
                &access,
                &crate::env::process_environment(),
                &held,
            );
            for (tool, declared_entry) in tools
                .iter_mut()
                .zip(crate::declared_access::reported_entries(&access))
            {
                if tool.get("mcp").is_some_and(|m| m.is_object()) {
                    let status = self.reach.status(&bot, tool, declared_entry);
                    tool["mcp"]["status"] = Value::String(status);
                }
            }
            if !tools.is_empty() {
                row.insert("tools".into(), Value::Array(tools));
            }
            let runtime = text_of(&entry["config"]["runtime"]);
            let harness = text_of(&entry["config"]["harness"]);
            let warnings = crate::mcp::warnings_for(&access, &runtime, &harness);
            if let Some(Value::Array(list)) = row.get_mut("warnings") {
                list.extend(warnings.into_iter().map(Value::String));
            }
        }
    }

    /// The bots due a periodic memory history report (every 5 minutes each, those hosted here).
    pub fn memory_due(&self, config: &Config, assignments: &[Value]) -> Vec<String> {
        let mine = config.runner_id();
        let mut at = self.memory_at.lock().unwrap();
        let mut due = vec![];
        for entry in assignments {
            let bot = text_of(&entry["bot"]);
            if bot.is_empty() || mine.is_empty() || text_of(&entry["runner_id"]) != mine {
                continue;
            }
            if at
                .get(&bot)
                .is_some_and(|t| t.elapsed() < crate::memory_history::EVERY)
            {
                continue;
            }
            at.insert(bot.clone(), Instant::now());
            if !due.contains(&bot) {
                due.push(bot);
            }
        }
        due
    }
}

/// The MCP servers one run passes to its harness: bot.yaml's, with every variable a server needs among the
/// turn's granted ones, then the granted MCP tools' (`from_grants`, crate::mcp::granted) that bot.yaml does not
/// name. Each server left out (no credential, or a transport the harness cannot take) is a line for the turn's
/// diagnostics, except a bot.yaml server whose grant brings it anyway: a grant needs no bot.yaml entry.
pub fn mcp_for_run(
    path: &Path,
    env: &Env,
    granted: &BTreeSet<String>,
    runtime: &str,
    harness: &str,
    proxied: &[crate::env::ProxiedTool],
    from_grants: &[Value],
) -> (Vec<Value>, Vec<String>) {
    let tools = crate::manifest::read(path)
        .map(|declared| crate::manifest::tools_of(&declared))
        .unwrap_or(Value::Null);
    let visible: Env = env
        .iter()
        .filter(|(k, _)| granted.contains(*k))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let (servers, problems) = crate::mcp::servers_for_run(&tools, &visible, proxied);
    let mut problems = crate::mcp::drop_covered(problems, &tools, from_grants);
    let servers = crate::mcp::merge(servers, from_grants);
    for (server, why) in crate::mcp::unsupported(&servers, runtime, harness) {
        problems.push(format!(
            "{}: MCP server not passed. {why}",
            text_of(&server["service"])
        ));
    }
    (crate::mcp::supported(&servers, runtime, harness), problems)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_run_gets_the_servers_it_holds_credentials_for_and_says_why_not_for_the_rest() {
        let dir =
            std::env::temp_dir().join(format!("tico-perbot-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("bot.yaml"),
            "tools:\n  - service: linear\n    env: LINEAR_KEY\n    mcp: {url: 'https://mcp.linear.app/mcp', headers: {Authorization: 'Bearer ${LINEAR_KEY}'}}\n  - service: notion\n    env: NOTION_KEY\n    mcp: {url: 'https://mcp.notion.com/mcp', headers: {Authorization: 'Bearer ${NOTION_KEY}'}}\n  - service: docs\n    mcp: {url: 'https://docs.example/sse', transport: sse}\n",
        )
        .unwrap();
        let env: Env = [("LINEAR_KEY", "lin"), ("NOTION_KEY", "from-the-process")]
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        let granted: BTreeSet<String> = ["LINEAR_KEY".to_string()].into_iter().collect();
        let (servers, problems) = mcp_for_run(&dir, &env, &granted, "codex", "", &[], &[]);
        assert_eq!(
            servers
                .iter()
                .map(|s| text_of(&s["name"]))
                .collect::<Vec<_>>(),
            ["linear"]
        );
        assert_eq!(problems.len(), 2, "{problems:?}");
        assert!(problems[0].starts_with("notion: MCP server not passed, NOTION_KEY is not granted"));
        assert!(problems[1].starts_with("docs: MCP server not passed. Codex cannot use it"));
        let (servers, _) = mcp_for_run(&dir, &env, &granted, "claude", "", &[], &[]);
        assert_eq!(servers.len(), 2);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_granted_mcp_tool_reaches_the_run_whatever_bot_yaml_says() {
        let dir =
            std::env::temp_dir().join(format!("tico-perbot-g-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        // bot.yaml names notion under a variable the bot does not hold; Tools grants it the notion MCP tool.
        std::fs::write(
            dir.join("bot.yaml"),
            "tools:\n  - service: notion\n    env: NOTION_KEY\n    mcp: {url: 'https://mcp.notion.com/mcp', headers: {Authorization: 'Bearer ${NOTION_KEY}'}}\n",
        )
        .unwrap();
        let grants = [
            json!({"name": "notion", "service": "notion", "url": "https://mcp.notion.com/mcp",
                             "transport": "http", "headers": {"Authorization": "Bearer ${NOTION_TOKEN}"}, "env": "NOTION_TOKEN"}),
        ];
        let (servers, problems) = mcp_for_run(
            &dir,
            &Env::new(),
            &BTreeSet::new(),
            "claude",
            "",
            &[],
            &grants,
        );
        assert_eq!(servers.len(), 1);
        assert_eq!(text_of(&servers[0]["env"]), "NOTION_TOKEN");
        // bot.yaml's account (NOTION_KEY) is another connection on that endpoint: still said to be missing.
        assert_eq!(problems.len(), 1, "{problems:?}");
        assert!(
            problems[0].starts_with("notion: MCP server not passed"),
            "{problems:?}"
        );
        // The grant of bot.yaml's very connection (its address and variable) leaves nothing missing.
        let same = [
            json!({"name": "notion", "service": "notion", "url": "https://mcp.notion.com/mcp",
                           "transport": "http", "headers": {"Authorization": "Bearer ${NOTION_KEY}"}, "env": "NOTION_KEY"}),
        ];
        let (_, problems) = mcp_for_run(
            &dir,
            &Env::new(),
            &BTreeSet::new(),
            "claude",
            "",
            &[],
            &same,
        );
        assert!(problems.is_empty(), "{problems:?}");
        // With no bot.yaml at all, the grant alone brings it.
        let none = dir.join("none");
        let (servers, problems) = mcp_for_run(
            &none,
            &Env::new(),
            &BTreeSet::new(),
            "claude",
            "",
            &[],
            &grants,
        );
        assert_eq!((servers.len(), problems.len()), (1, 0));
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn memory_reports_come_round_every_five_minutes_per_bot_hosted_here() {
        let dir =
            std::env::temp_dir().join(format!("tico-perbot-cfg-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("runner.json"),
            json!({"runner_id": "r1"}).to_string(),
        )
        .unwrap();
        let config = Config::load(&dir.join("runner.json")).unwrap();
        let per = PerBot::default();
        let rows = [
            json!({"bot": "a", "runner_id": "r1"}),
            json!({"bot": "b", "runner_id": "r2"}),
        ];
        assert_eq!(per.memory_due(&config, &rows), ["a"]);
        assert!(per.memory_due(&config, &rows).is_empty());
        std::fs::remove_dir_all(dir).ok();
    }
}
