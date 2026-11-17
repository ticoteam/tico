//! `tico task worktree add|attach|setup` inside a turn: clients/commands/task_work.py and clients/remotecli.py run
//! `tico-runner worktree <operation>`, so a bot's own worktree is made with the same code, records and local proof
//! as one the runner prepares for a waiting task (`prepare`). It runs as the turn's user with the turn's own
//! environment: its Tico token (`HUB_TOKEN`), workspace, task and git helper. It never reads runner.json.
//!
//! It prints one JSON object: the link on success (exit 0), else `{"error": {...}}` (exit 1), with the server's
//! `code`, `status`, `retryable`, `operation_id` and extra fields whenever Tico answered with an error or could not be
//! reached (a worktree wait, `worktree_limit`; a 503 or lost connection, retryable), or only a `detail` for everything
//! else.

use std::path::Path;

use serde_json::{json, Map, Value};

use super::*;
use crate::client::{ApiError, Client};

/// Why the command failed: the server's refusal as it gave it, or a message for the bot.
#[derive(Debug)]
pub enum Failure {
    Api(ApiError),
    Wt(WtErr),
    Msg(String),
}

impl From<ApiError> for Failure {
    fn from(e: ApiError) -> Failure {
        Failure::Api(e)
    }
}

impl From<WtErr> for Failure {
    fn from(e: WtErr) -> Failure {
        match e {
            // Tico's answer from inside a checkout or setup step stays a refusal: a 503 or a lost connection is
            // still one to retry.
            WtErr::Api(e) => Failure::Api(e),
            other => Failure::Wt(other),
        }
    }
}

impl From<String> for Failure {
    fn from(m: String) -> Failure {
        Failure::Msg(m)
    }
}

impl Failure {
    pub fn to_json(&self) -> Value {
        match self {
            Failure::Api(e) => {
                let mut out = e.extra.clone();
                out.insert("code".into(), json!(e.code));
                out.insert("detail".into(), json!(e.detail));
                out.insert("status".into(), json!(e.status));
                out.insert("retryable".into(), json!(e.retryable));
                if let Some(id) = &e.operation_id {
                    out.insert("operation_id".into(), json!(id));
                }
                json!({ "error": out })
            }
            Failure::Wt(e) => json!({"error": {"detail": e.text()}}),
            Failure::Msg(m) => json!({"error": {"detail": m}}),
        }
    }
}

fn msg(text: &str) -> Failure {
    Failure::Msg(text.into())
}

/// A git network failure through the computer's own git login, said as the runner says it (`prepare`).
fn through(e: Failure, repo: &Map<String, Value>, env: &Env) -> Failure {
    match e {
        Failure::Wt(WtErr::Msg(_, Some(detail)))
            if crate::util::truthy(repo.get("machine_git")) =>
        {
            Failure::Msg(repositories::machine_error(
                &own(repo, "full_name"),
                &detail,
                env,
            ))
        }
        other => other,
    }
}

/// `tico-runner worktree <operation> <value> [--task T]`: run it with this process's environment and print the result.
pub fn main(operation: &str, value: &str, task: Option<&str>) -> i32 {
    let turn: Env = std::env::vars().collect();
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime");
    match rt.block_on(run(operation, value, task, &turn)) {
        Ok(out) => {
            println!("{out}");
            0
        }
        Err(e) => {
            println!("{}", e.to_json());
            1
        }
    }
}

/// The turn's workspace, client and canonical task, then the operation.
pub async fn run(
    operation: &str,
    value: &str,
    task: Option<&str>,
    turn: &Env,
) -> Result<Value, Failure> {
    run_with_backoff(operation, value, task, turn, 1.0).await
}

/// `run`, with the client's waits between retries scaled by `backoff` (tests use a small one).
pub(crate) async fn run_with_backoff(
    operation: &str,
    value: &str,
    task: Option<&str>,
    turn: &Env,
    backoff: f64,
) -> Result<Value, Failure> {
    let get = |k: &str| turn.get(k).cloned().filter(|v| !v.is_empty());
    let explicit = task.is_some();
    let task = task
        .filter(|t| !t.is_empty())
        .map(String::from)
        .or_else(|| get("HUB_TASK_ID"));
    let (Some(workspace), Some(_), Some(task)) = (get("HUB_WORKSPACE"), get("HUB_BOT"), task)
    else {
        return Err(msg(
            "Task worktrees need a bot run and a task; use --task when this run has no current task",
        ));
    };
    let mut client = Client::new(
        &get("HUB_API_URL").unwrap_or_default(),
        &get("HUB_TOKEN").unwrap_or_default(),
        15,
        3,
    )?;
    client.backoff_scale = backoff;
    let task = if explicit {
        canonical_task_id(&client, &task).await?
    } else {
        task
    };
    match command(
        &client,
        operation,
        value,
        &task,
        Path::new(&workspace),
        turn,
    )
    .await
    {
        Err(Failure::Api(e)) if e.status == 404 || e.status == 405 => {
            Err(msg("The server is too old for task worktrees; update Tico"))
        }
        other => other,
    }
}

/// A task named on the command line, as the server's canonical id: a UUID as it is, a number or short id looked up.
async fn canonical_task_id(client: &Client, task: &str) -> Result<String, Failure> {
    static UUID: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let uuid = UUID.get_or_init(|| {
        Regex::new(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
            .unwrap()
    });
    if uuid.is_match(task) {
        return Ok(task.to_lowercase());
    }
    let found = client.get(&format!("tasks/{}", quote(task))).await?;
    match found["task"]["id"].as_str().filter(|id| !id.is_empty()) {
        Some(id) => Ok(id.to_string()),
        None => Err(msg("The task id could not be resolved to its canonical id")),
    }
}

/// Percent-encode everything but unreserved characters (urllib.parse.quote with safe='').
fn quote(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// The repository as the server lists it for this run, which must be writable; or, when the bot has no
/// GitHub App repositories, the computer's own git login (`machine_git`).
async fn metadata(client: &Client, name: &str) -> Result<Map<String, Value>, Failure> {
    let reply = match client.get("runners/me/repositories").await {
        Ok(reply) => reply,
        Err(e) if e.status == 404 => json!({"repositories": []}),
        Err(e) => return Err(e.into()),
    };
    let repos = reply["repositories"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    if reply["configured"] == json!(false)
        || (repos.is_empty() && reply["configured"] != json!(true))
    {
        repositories::address(name)?;
        let mut repo = Map::new();
        repo.insert("full_name".into(), json!(name));
        repo.insert("machine_git".into(), json!(true));
        return Ok(repo);
    }
    let listed = repos
        .iter()
        .find(|r| text_of(&r["full_name"]).to_lowercase() == name.to_lowercase());
    if let Some(repo) = listed.filter(|r| r["access"] == "write") {
        return Ok(repo.as_object().cloned().unwrap_or_default());
    }
    // Say what the server listed for this run, so a mismatch with the bot's grant settings shows which side differs.
    let mut names: Vec<String> = repos
        .iter()
        .filter(|r| r["access"] == "write")
        .map(|r| text_of(&r["full_name"]))
        .collect();
    names.sort();
    let mut writable = names
        .iter()
        .take(10)
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");
    if names.len() > 10 {
        writable.push_str(&format!(" and {} more", names.len() - 10));
    }
    if writable.is_empty() {
        writable = "none".into();
    }
    let listed = if listed.is_some() {
        format!("lists {name} as read-only")
    } else {
        format!("does not list {name}")
    };
    Err(Failure::Msg(format!(
        "This bot needs write access to this repository: Tico {listed} for this run (writable here: {writable})"
    )))
}

/// A link row with its detail folded in, the detail winning (`{**row, **_detail(row)}`): runner rows keep
/// `detail_json`, public task-link rows `detail`.
fn flat(row: &Value) -> Map<String, Value> {
    let mut out = row.as_object().cloned().unwrap_or_default();
    let detail = match row.get("detail_json").or_else(|| row.get("detail")) {
        Some(Value::String(s)) => serde_json::from_str::<Value>(s).unwrap_or(Value::Null),
        Some(other) => other.clone(),
        None => Value::Null,
    };
    if let Some(d) = detail.as_object() {
        out.extend(d.clone());
    }
    out
}

/// Ask the supervisor, through the turn's credential socket, to refresh the computer's mirror of `repo` first,
/// as the runner does before it prepares a worktree. Without one the base clone is used as its last fetch left it.
fn refresh_mirror(repo: &mut Map<String, Value>, turn: &Env) {
    let Some(socket) = turn
        .get(crate::gitcred::SOCKET_ENV)
        .filter(|s| !s.is_empty())
    else {
        return;
    };
    if crate::util::truthy(repo.get("machine_git")) {
        return;
    }
    let token =
        crate::client::bearer(turn.get("HUB_TOKEN").map_or("", String::as_str)).unwrap_or_default();
    let asked = json!({"token": token, "repository": own(repo, "full_name"), "refresh": true});
    let reply = ask_socket(Path::new(socket), &asked);
    if let Ok(reply) = reply {
        if crate::util::truthy(reply.get("refreshed")) || crate::util::truthy(reply.get("cached")) {
            if !text_of(&reply["warning"]).is_empty() {
                repo.insert("fetch_warning".into(), reply["warning"].clone());
            }
            repo.insert("_mirror_refresh".into(), reply);
        }
    }
}

/// One request to the supervisor's credential socket and its one-line JSON answer (60 s: a refresh fetches).
fn ask_socket(socket: &Path, asked: &Value) -> std::io::Result<Value> {
    use std::io::{BufRead, Read, Write};
    let mut stream = std::os::unix::net::UnixStream::connect(socket)?;
    stream.set_read_timeout(Some(Duration::from_secs(60)))?;
    stream.write_all(format!("{asked}\n").as_bytes())?;
    let mut line = String::new();
    std::io::BufReader::new(stream.take(4096)).read_line(&mut line)?;
    Ok(serde_json::from_str(&line).unwrap_or(Value::Null))
}

/// The managed base clone for `repo`, refreshed from the computer's mirror when the supervisor can.
async fn base_for(
    workspace: &Path,
    repo: &mut Map<String, Value>,
    env: &Env,
    turn: &Env,
) -> Result<(PathBuf, String), Failure> {
    refresh_mirror(repo, turn);
    Ok(repositories::worktree_base(workspace, repo, env).await?)
}

/// What the retired computers left behind, for the bot: unpushed commits and changed files stay there.
fn recovery_warning(link: &Map<String, Value>) -> String {
    let mut history: Vec<Value> = link
        .get("retired_worktrees")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if history.is_empty() {
        history.push(link.get("retired_worktree").cloned().unwrap_or(json!({})));
    }
    let mut notes = vec![own(link, "recovery_note")];
    for old in history {
        let at = text_of(&old["computer_id"]);
        notes.push(text_of(&old["recovery_note"]));
        if crate::util::truthy(old.get("ahead")) {
            let head = Some(text_of(&old["last_commit"]))
                .filter(|h| !h.is_empty())
                .unwrap_or_else(|| "unknown".into());
            notes.push(format!(
                "{} unpushed commits stayed on the retired computer ({at}); last head {head}.",
                text_of(&old["ahead"])
            ));
        }
        if crate::util::truthy(old.get("dirty_files")) {
            notes.push(format!(
                "{} changed files stayed on the retired computer ({at}).",
                text_of(&old["dirty_files"])
            ));
        }
    }
    notes.retain(|n| !n.is_empty());
    notes.join(" ")
}

const RECOVERY_NOTE: &str = "The old branch is missing on origin. Old work was not published there; starting a new branch from the default branch.";

/// `link` with `fields` set, as the command's answer.
fn answer(link: &Value, fields: Value) -> Value {
    let mut out = link.as_object().cloned().unwrap_or_default();
    if let Some(f) = fields.as_object() {
        out.extend(f.clone());
    }
    Value::Object(out)
}

fn with_warning(mut out: Value, warning: &str) -> Value {
    if !warning.is_empty() {
        out["warning"] = json!(warning);
    }
    out
}

async fn deleted_files(path: &Path, env: &Env) -> R<bool> {
    Ok(!git(path, env, &["ls-files", "--deleted", "-z"], true)
        .await?
        .stdout
        .is_empty())
}

/// One operation for `task` in `workspace` (worktrees.command, which this replaced).
pub async fn command(
    client: &Client,
    operation: &str,
    value: &str,
    task: &str,
    workspace: &Path,
    turn: &Env,
) -> Result<Value, Failure> {
    match operation {
        "add" => add(client, value, task, workspace, turn).await,
        "attach" => attach(client, value, task, workspace, turn).await,
        "setup" => setup_again(client, value, task, workspace, turn).await,
        _ => Err(msg("Use add, attach or setup")),
    }
}

/// The git environment for `repo`: the computer's own git login, or the turn's repository helper.
fn git_env(repo: &Map<String, Value>, turn: &Env) -> Env {
    if crate::util::truthy(repo.get("machine_git")) {
        crate::safegit::machine_environment(turn)
    } else {
        crate::safegit::environment(turn)
    }
}

/// `tico task worktree add`: register the task's worktree, then make, resume or verify it and run its setup.
async fn add(
    client: &Client,
    name: &str,
    task: &str,
    workspace: &Path,
    turn: &Env,
) -> Result<Value, Failure> {
    disk_floor(workspace)?;
    let mut repo = metadata(client, name).await?;
    let env = git_env(&repo, turn);
    let link = client
        .post(
            &format!("tasks/{task}/worktrees"),
            &json!({"repo": own(&repo, "full_name")}),
            None,
        )
        .await?;
    let made = add_registered(client, &link, &mut repo, task, workspace, &env, turn)
        .await
        .map_err(|e| through(e, &repo, &env));
    if let Err(Failure::Msg(_) | Failure::Wt(_)) = &made {
        // Nothing usable was made: the server shows the worktree pending, not as whatever it last was.
        let checked_out =
            safe_path(workspace, &text_of(&link["path"])).is_ok_and(|p| p.join(".git").is_file());
        if !checked_out {
            let _ = client
                .patch(
                    &format!("tasks/{task}/links/{}", text_of(&link["link_id"])),
                    &json!({"state": "pending"}),
                )
                .await;
        }
    }
    made
}

async fn add_registered(
    client: &Client,
    link: &Value,
    repo: &mut Map<String, Value>,
    task: &str,
    workspace: &Path,
    env: &Env,
    turn: &Env,
) -> Result<Value, Failure> {
    let rel = text_of(&link["path"]);
    let path = safe_path(workspace, &rel)?;
    let _lock = locked(workspace, &rel).await?;
    checked_branch(&text_of(&link["branch"])).await?;
    let mut l = flat(link);
    let name = own(repo, "full_name");
    let machine_git = crate::util::truthy(repo.get("machine_git"));
    let mut state = own(&l, "checkout_state");
    let mut setup_pending = crate::util::truthy(l.get("setup_pending"));
    if state == "queued" && l.get("recovery").is_some_and(Value::is_object) {
        if path.exists() {
            return Err(msg(
                "Worktree path already exists without an initialization record; kept unchanged",
            ));
        }
        let (base, default) = base_for(workspace, repo, env, turn).await?;
        recover_retired(&base, &default, &mut l, task, env, client).await?;
        let fallback = l.get("recovery").map(|r| text_of(&r["fallback_branch"]));
        if fallback.is_some_and(|f| f == own(&l, "branch")) {
            l.insert("recovery_note".into(), json!(RECOVERY_NOTE));
        }
    }
    let recovered = recovery_warning(&l);
    // The link as the server registered it, with the branch and note a recovery changed.
    let link = &Value::Object(l.clone());
    let branch = checked_branch(&own(&l, "branch")).await?;
    let shown = path.display().to_string();
    if state.is_empty() && path.exists() {
        verify_worktree(
            &path,
            workspace,
            &name,
            machine_git,
            &branch,
            env,
            None,
            false,
        )
        .await?;
        if link["state"] == "present" && !setup_pending && !deleted_files(&path, env).await? {
            return Ok(answer(
                link,
                json!({"state": "present", "checkout_state": "legacy_present", "setup_pending": false, "workspace_path": shown}),
            ));
        }
        return Ok(answer(
            link,
            json!({"state": "pending", "checkout_state": "unverified", "setup_pending": true, "workspace_path": shown,
                   "warning": "Legacy worktree has no completion proof; kept unchanged and not reported ready"}),
        ));
    }
    if state.is_empty() {
        state = "queued".into();
    }
    let c = Checkout {
        workspace,
        repo: &name,
        machine_git,
        branch: &branch,
        env,
    };
    let mut expected_base = own(&l, "expected_base");
    if path.exists() {
        if state == "queued" {
            return Err(msg(
                "Worktree path already exists without an initialization record; kept unchanged",
            ));
        }
        let (base, default) = base_for(workspace, repo, env, turn).await?;
        match state.clone().as_str() {
            "initializing" => {
                let (head, target, base_id) =
                    create_or_resume(&path, &c, &base, &default, &l, task, client).await?;
                state = "checkout_ready".into();
                if expected_base.is_empty() {
                    expected_base = base_id;
                }
                l.insert("expected_head".into(), json!(head));
                l.insert("checkout_target".into(), json!(target));
            }
            "checkout_ready" => {
                let (head, target) = (own(&l, "expected_head"), own(&l, "checkout_target"));
                if expected_base.is_empty() || head.is_empty() || target.is_empty() {
                    return Err(msg(
                        "Checkout lacks recorded base and commit identity; kept unchanged",
                    ));
                }
                let identity = marker_identity(
                    &own(&l, "link_id"),
                    task,
                    &rel,
                    &name,
                    &branch,
                    &head,
                    &target,
                    &expected_base,
                );
                resume_interrupted(&path, &c, &head, &expected_base, &identity).await?;
            }
            "setup_running" | "setup_failed" => {
                return Err(msg(
                    "Setup did not finish; use tico task worktree setup to retry explicitly",
                ))
            }
            "ready" => {
                if expected_base.is_empty() {
                    return Err(msg(
                        "Ready worktree has no recorded base identity; kept unchanged",
                    ));
                }
                verify_worktree(
                    &path,
                    workspace,
                    &name,
                    machine_git,
                    &branch,
                    env,
                    Some(&expected_base),
                    false,
                )
                .await?;
                let mut row = l.clone();
                row.entry("task_id").or_insert_with(|| json!(task));
                verify_ready_registration(workspace, &Value::Object(row), &l, &name).await?;
                return Ok(with_warning(
                    answer(
                        link,
                        json!({"state": "present", "checkout_state": "ready", "workspace_path": shown}),
                    ),
                    &recovered,
                ));
            }
            "legacy_present" => {
                verify_worktree(
                    &path,
                    workspace,
                    &name,
                    machine_git,
                    &branch,
                    env,
                    None,
                    false,
                )
                .await?;
                if deleted_files(&path, env).await? {
                    return Err(msg(
                        "Legacy worktree has missing tracked files; kept unchanged and unverified",
                    ));
                }
                if link["state"] != "present" || setup_pending {
                    return Ok(answer(
                        link,
                        json!({"state": "pending", "checkout_state": "legacy_present", "setup_pending": setup_pending,
                               "workspace_path": shown,
                               "warning": "Legacy worktree is not recorded present with setup complete; kept unchanged"}),
                    ));
                }
                return Ok(answer(
                    link,
                    json!({"state": "present", "checkout_state": "legacy_present", "setup_pending": false,
                           "workspace_path": shown}),
                ));
            }
            _ => {
                return Err(msg(
                    "Worktree initialization is not verified; kept it unchanged",
                ))
            }
        }
    } else {
        let (base, default) = base_for(workspace, repo, env, turn).await?;
        if state != "queued" && state != "initializing" {
            return Err(msg(
                "Missing worktree is not in a resumable initialization state",
            ));
        }
        l.insert("checkout_state".into(), json!(state));
        let (head, target, base_id) =
            create_or_resume(&path, &c, &base, &default, &l, task, client).await?;
        state = "checkout_ready".into();
        setup_pending = true;
        expected_base = base_id;
        l.insert("expected_head".into(), json!(head));
        l.insert("checkout_target".into(), json!(target));
    }
    if state == "checkout_ready" || setup_pending {
        l.insert("checkout_state".into(), json!(state));
        l.insert("expected_base".into(), json!(expected_base));
        run_registered_setup(&path, &c, &own(repo, "setup_command"), &l, task, client).await?;
    } else if state != "ready" {
        return Err(msg(
            "Worktree is not ready; no initialization or setup completion was recorded",
        ));
    }
    let warning = [recovered, own(repo, "fetch_warning")]
        .into_iter()
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    Ok(with_warning(
        answer(
            link,
            json!({"state": "present", "checkout_state": "ready", "setup_pending": false, "workspace_path": shown}),
        ),
        &warning,
    ))
}

/// `tico task worktree setup`: run (or retry) the setup of the task's worktree of `name`.
async fn setup_again(
    client: &Client,
    name: &str,
    task: &str,
    workspace: &Path,
    turn: &Env,
) -> Result<Value, Failure> {
    let mut repo = metadata(client, name).await?;
    let env = git_env(&repo, turn);
    setup_registered(client, name, &mut repo, task, workspace, &env, turn)
        .await
        .map_err(|e| through(e, &repo, &env))
}

async fn setup_registered(
    client: &Client,
    name: &str,
    repo: &mut Map<String, Value>,
    task: &str,
    workspace: &Path,
    env: &Env,
    turn: &Env,
) -> Result<Value, Failure> {
    let rows = client.get(&format!("tasks/{task}/links")).await?;
    let rows = rows
        .get("links")
        .unwrap_or(&rows)
        .as_array()
        .cloned()
        .unwrap_or_default();
    let needs_setup = |l: &Map<String, Value>| {
        crate::util::truthy(l.get("setup_pending"))
            || [
                "checkout_ready",
                "setup_failed",
                "setup_running",
                "initializing",
            ]
            .contains(&own(l, "checkout_state").as_str())
    };
    let mut candidates: Vec<&Value> = rows
        .iter()
        .filter(|r| {
            let l = flat(r);
            r["kind"] == "worktree"
                && text_of(&r["repo"]).to_lowercase() == name.to_lowercase()
                && ["present", "pending"].contains(&text_of(&r["state"]).as_str())
                && own(&l, "checkout_state") != "legacy_present"
        })
        .collect();
    candidates.sort_by_key(|r| !needs_setup(&flat(r)));
    let Some(row) = candidates.first().copied() else {
        return Err(msg(
            "No worktree needing setup for this repository on the task",
        ));
    };
    let rel = text_of(&row["path"]);
    let path = safe_path(workspace, &rel)?;
    let _lock = locked(workspace, &rel).await?;
    let mut l = flat(row);
    l.insert("link_id".into(), row["id"].clone());
    let full_name = own(repo, "full_name");
    let machine_git = crate::util::truthy(repo.get("machine_git"));
    let branch = checked_branch(&text_of(&row["branch"])).await?;
    let c = Checkout {
        workspace,
        repo: &full_name,
        machine_git,
        branch: &branch,
        env,
    };
    let state = own(&l, "checkout_state");
    if state == "initializing" {
        let (base, default) = base_for(workspace, repo, env, turn).await?;
        let (head, target, base_id) =
            create_or_resume(&path, &c, &base, &default, &l, task, client).await?;
        l.insert("checkout_state".into(), json!("checkout_ready"));
        l.insert("expected_head".into(), json!(head));
        l.insert("checkout_target".into(), json!(target));
        if own(&l, "expected_base").is_empty() {
            l.insert("expected_base".into(), json!(base_id));
        }
    }
    if !path.exists() {
        return Err(msg("Worktree is missing; restore it before setup"));
    }
    let shown = path.display().to_string();
    if state == "ready" {
        verify_worktree(
            &path,
            workspace,
            &full_name,
            machine_git,
            &branch,
            env,
            Some(&own(&l, "expected_base")),
            false,
        )
        .await?;
        let mut checked = row.clone();
        if text_of(&checked["task_id"]).is_empty() {
            checked["task_id"] = json!(task);
        }
        verify_ready_registration(workspace, &checked, &l, &full_name).await?;
        return Ok(answer(row, json!({"workspace_path": shown})));
    }
    run_registered_setup(&path, &c, &own(repo, "setup_command"), &l, task, client).await?;
    Ok(answer(
        row,
        json!({"state": "present", "checkout_state": "ready", "setup_pending": false, "workspace_path": shown}),
    ))
}

/// `tico task worktree attach`: register a Git worktree already inside the team workspace, then certify it.
async fn attach(
    client: &Client,
    value: &str,
    task: &str,
    workspace: &Path,
    turn: &Env,
) -> Result<Value, Failure> {
    let root = resolve(workspace);
    let raw = Path::new(value);
    let given = if raw.is_absolute() {
        raw.strip_prefix(&root)
            .map_err(|_| msg("Use a worktree inside the team workspace"))?
            .display()
            .to_string()
    } else {
        value.to_string()
    };
    let path = safe_path(workspace, &given)?;
    let rel = path
        .strip_prefix(&root)
        .map_err(|_| msg("Use a worktree inside the team workspace"))?
        .display()
        .to_string();
    if !path.join(".git").is_file() {
        return Err(msg("Attach needs a Git worktree, not a base clone"));
    }
    // Which repository it is, read with the computer's own settings before the repository is known.
    let probe = crate::safegit::machine_environment(turn);
    let common = resolve(Path::new(
        git(
            &path,
            &probe,
            &["rev-parse", "--path-format=absolute", "--git-common-dir"],
            true,
        )
        .await?
        .stdout
        .trim(),
    ));
    if !common.starts_with(&root) || common == root {
        return Err(msg("Worktree base must be inside the team workspace"));
    }
    let origin = git(
        &path,
        &probe,
        &["config", "--get", "remote.origin.url"],
        true,
    )
    .await?
    .stdout
    .trim()
    .to_string();
    let named = crate::gitcred::repository_name(&origin).unwrap_or_else(|| origin.clone());
    let repo = metadata(client, &named).await?;
    let env = git_env(&repo, turn);
    attach_registered(client, &path, &rel, &repo, task, workspace, &env)
        .await
        .map_err(|e| through(e, &repo, &env))
}

async fn attach_registered(
    client: &Client,
    path: &Path,
    rel: &str,
    repo: &Map<String, Value>,
    task: &str,
    workspace: &Path,
    env: &Env,
) -> Result<Value, Failure> {
    let full_name = own(repo, "full_name");
    let branch = checked_branch(
        git(path, env, &["symbolic-ref", "--short", "HEAD"], true)
            .await?
            .stdout
            .trim(),
    )
    .await?;
    let link = client
        .post(
            &format!("tasks/{task}/worktrees/attach"),
            &json!({"path": rel, "repo": full_name, "branch": branch}),
            None,
        )
        .await?;
    let mut row = flat(&link);
    row.insert("id".into(), link["link_id"].clone());
    row.insert("task_id".into(), json!(task));
    let (head, target, base) = record_attached(
        workspace,
        &Value::Object(row),
        &text_of(&link["branch"]),
        &full_name,
        crate::util::truthy(repo.get("machine_git")),
        env,
    )
    .await?;
    client
        .patch(
            &format!("tasks/{task}/links/{}", text_of(&link["link_id"])),
            &json!({"state": "present", "path": link["path"], "setup_pending": false, "checkout_state": "ready",
                    "expected_head": head, "checkout_target": target, "expected_base": base}),
        )
        .await?;
    Ok(with_warning(
        answer(&link, json!({"workspace_path": path.display().to_string()})),
        &own(repo, "fetch_warning"),
    ))
}
