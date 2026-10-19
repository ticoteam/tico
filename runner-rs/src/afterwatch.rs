//! Background commands a Claude turn left running when it ended on its wait (hosts::claude `end_waiting`). They are
//! watched here until they end, then the bot gets one wake for the turn, carrying each command's exit code and the
//! end of its output (`POST attempts/{aid}/background-done`). One still running at the run's time limit is killed.
//! The list lives in the runner's state directory, so a restarted runner carries on watching.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};

use crate::redact::Redactor;
use crate::runner::Runner;
use crate::util::{epoch_s, log, text_of};

const EVERY: Duration = Duration::from_secs(10);
/// How much of each command's output the wake carries.
const TAIL_CHARS: usize = 2000;
const FILE: &str = "background.json";

/// One turn's kept commands: `{aid, bot, session, status_file, deadline, commands: [{task_id, description, command,
/// ci_watch, pid, output_file, kept_at, exit, killed, ended}]}`. `ended` is set once the command is over.
struct Watched {
    turns: Vec<Value>,
    /// Each run's secrets, while this runner still has them (not across a restart).
    redactors: std::collections::HashMap<String, Arc<Redactor>>,
}

fn watched() -> &'static Mutex<Watched> {
    static W: std::sync::OnceLock<Mutex<Watched>> = std::sync::OnceLock::new();
    W.get_or_init(|| Mutex::new(Watched { turns: vec![], redactors: Default::default() }))
}

fn save(dir: &Path, turns: &[Value]) {
    let path = dir.join(FILE);
    if turns.is_empty() {
        let _ = std::fs::remove_file(path);
        return;
    }
    let tmp = dir.join(format!("{FILE}.tmp"));
    if std::fs::write(&tmp, Value::Array(turns.to_vec()).to_string()).is_ok() {
        let _ = std::fs::rename(tmp, path);
    }
}

/// Watch the shells a turn kept (the host's `background_kept` event). `deadline`: the run's time limit, epoch seconds.
pub fn keep(dir: &Path, aid: &str, bot: &str, session: &str, deadline: f64, event: &Value, redactor: Option<&Arc<Redactor>>) {
    let now = epoch_s();
    let commands: Vec<Value> = event["kept"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|k| {
            let mut c = k.clone();
            c["kept_at"] = json!(now);
            c
        })
        .collect();
    if commands.is_empty() {
        return;
    }
    let mut w = watched().lock().unwrap();
    w.turns.push(json!({"aid": aid, "bot": bot, "session": session, "status_file": event["status_file"], "deadline": deadline, "commands": commands}));
    if let Some(r) = redactor {
        w.redactors.insert(aid.into(), r.clone());
    }
    save(dir, &w.turns);
}

/// One line for a new turn on a session whose earlier turn left commands running, so the model does not start
/// them again. Claude itself says on resuming that they did not finish, which is wrong.
pub fn still_running(bot: &str, session: &str) -> Option<String> {
    let w = watched().lock().unwrap();
    let running: Vec<String> = w
        .turns
        .iter()
        .filter(|t| t["bot"] == bot && t["session"] == session)
        .flat_map(|t| t["commands"].as_array().cloned().unwrap_or_default())
        .filter(|c| c["ended"].is_null())
        .map(|c| format!("\"{}\" (kept since {})", text_of(&c["description"]), text_of(&crate::util::iso(&c["kept_at"])).replace("+00:00", "Z")))
        .collect();
    (!running.is_empty()).then(|| {
        format!(
            "Background commands from an earlier turn are still running: {}. If Claude reports they did not finish, that is wrong: the runner kept them, and you get one message with each exit code and the end of its output when they end. Do not start them again.",
            running.join("; ")
        )
    })
}

/// The process `pid` is still the kept shell: alive, and writing to its output file.
fn holds(pid: u32, output_file: &str) -> bool {
    #[cfg(target_os = "linux")]
    {
        std::fs::read_link(format!("/proc/{pid}/fd/1")).is_ok_and(|o| o.display().to_string() == output_file)
    }
    #[cfg(not(target_os = "linux"))]
    {
        crate::hosts::claude::stdout_of(pid).is_some_and(|o| o == output_file)
    }
}

/// The last `TAIL_CHARS` characters of a command's output.
fn tail(path: &str) -> String {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut f) = std::fs::File::open(path) else { return String::new() };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(4 * TAIL_CHARS as u64)));
    let mut bytes = vec![];
    let _ = f.read_to_end(&mut bytes);
    let text = String::from_utf8_lossy(&bytes);
    let n = text.chars().count();
    text.chars().skip(n.saturating_sub(TAIL_CHARS)).collect()
}

/// Ends a kept command and everything it runs, not just its shell: a suite's runner and workers would run on. That is
/// the shell's process group (Claude starts each background shell as a group leader) and every process under the
/// shell, all found before any is signalled. A job another command started is in neither and is left alone.
fn kill_workload(pid: u32) {
    let parent = crate::hosts::claude::parents();
    let mut tree = vec![pid];
    let mut i = 0;
    while i < tree.len() {
        let at = tree[i];
        tree.extend(parent.iter().filter(|(_, &p)| p == at).map(|(&c, _)| c));
        i += 1;
    }
    // SAFETY: the kept shell, its group and the processes under it, found a moment ago; the shell is not reaped yet.
    unsafe {
        if libc::getpgid(pid as i32) == pid as i32 {
            libc::kill(-(pid as i32), libc::SIGKILL);
        }
        for p in tree {
            libc::kill(p as i32, libc::SIGKILL);
        }
    }
}

/// Moves one turn's commands on; true when every one has ended.
fn poll(turn: &mut Value, now: f64) -> bool {
    let codes = turn["status_file"].as_str().map(|p| crate::reap::statuses(Path::new(p))).unwrap_or_default();
    let late = turn["deadline"].as_f64().is_some_and(|d| now >= d);
    let reaped = !turn["status_file"].is_null();
    let Some(commands) = turn["commands"].as_array_mut() else { return true };
    for c in commands.iter_mut().filter(|c| c["ended"].is_null()) {
        let pid = c["pid"].as_u64().unwrap_or(0) as u32;
        if let Some(code) = codes.get(&pid) {
            c["exit"] = json!(code);
            c["ended"] = json!(now);
        } else if pid == 0 || !holds(pid, &text_of(&c["output_file"])) {
            c["ended"] = json!(now); // its exit code is not known (no reaping parent, or the parent went)
        } else if late && c["killed"].is_null() {
            // Its reaping parent has not reaped it, so the pid is still this shell's.
            kill_workload(pid);
            c["killed"] = json!(true);
            if !reaped {
                c["ended"] = json!(now);
            }
        }
    }
    commands.iter().all(|c| !c["ended"].is_null())
}

/// What the wake says about one turn's commands.
fn report(turn: &Value, redactor: Option<&Arc<Redactor>>) -> Value {
    let scrub = |t: &str| redactor.map(|r| r.scrub_text(t)).unwrap_or_else(|| t.to_string());
    let commands: Vec<Value> = turn["commands"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|c| {
            json!({
                "description": scrub(&text_of(&c["description"])).chars().take(300).collect::<String>(),
                "exit": c["exit"],
                "killed": c["killed"] == true,
                "ci_watch": c["ci_watch"],
                "tail": scrub(&tail(&text_of(&c["output_file"]))),
            })
        })
        .collect();
    json!({"commands": commands})
}

async fn tick(runner: &Runner) {
    let dir = runner.state.directory.clone();
    let now = epoch_s();
    let (done, redactors) = {
        let mut w = watched().lock().unwrap();
        let before = w.turns.clone();
        let mut done = vec![];
        for t in w.turns.iter_mut() {
            if poll(t, now) {
                done.push(t.clone());
            }
        }
        if w.turns != before {
            save(&dir, &w.turns);
        }
        (done, w.redactors.clone())
    };
    for turn in done {
        let aid = text_of(&turn["aid"]);
        let body = report(&turn, redactors.get(&aid));
        let sent = runner.client.post(&format!("attempts/{aid}/background-done"), &body, Some(&format!("background-done:{aid}"))).await;
        if let Err(e) = &sent {
            if e.retryable {
                continue; // the next tick tries again
            }
            log(&format!("Tico runner: {}: the wake for background commands of {aid} was refused: {}", text_of(&turn["bot"]), e.detail));
        }
        let mut w = watched().lock().unwrap();
        w.turns.retain(|t| t["aid"] != turn["aid"]);
        w.redactors.remove(&aid);
        save(&dir, &w.turns);
        if let Some(p) = turn["status_file"].as_str() {
            let _ = std::fs::remove_file(p);
        }
    }
}

/// Watches until the runner stops, starting with what an earlier runner left in the state directory.
pub async fn run_loop(runner: Arc<Runner>) {
    let path: PathBuf = runner.state.directory.join(FILE);
    if let Some(turns) = std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Vec<Value>>(&t).ok()) {
        watched().lock().unwrap().turns.extend(turns);
    }
    let mut ticker = tokio::time::interval(EVERY);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    while !runner.stopping() {
        ticker.tick().await;
        tick(&runner).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::process::ExitStatusExt;

    #[test]
    fn a_kept_command_is_reported_once_it_ends_and_killed_at_the_runs_limit() {
        let dir = std::env::temp_dir().join(format!("afterwatch-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let out = dir.join("s1.output");
        // A kept shell: writing to its output file, its exit code to come from its reaping parent's status file.
        let mut shell = std::process::Command::new("sh").arg("-c").arg("echo '3790 passed'; exec sleep 30").stdout(std::fs::File::create(&out).unwrap()).spawn().unwrap();
        let status = dir.join("status");
        std::fs::write(&status, "").unwrap();
        let event = json!({"kept": [{"task_id": "s1", "description": "Run the full suite", "pid": shell.id(), "output_file": out.display().to_string()}], "status_file": status});
        keep(&dir, "a1", "acme-afterwatch", "task:t1", epoch_s() + 3600.0, &event, None);
        std::thread::sleep(Duration::from_millis(100));
        let line = still_running("acme-afterwatch", "task:t1").expect("a line for the next turn");
        assert!(line.contains("\"Run the full suite\""), "{line}");
        assert!(still_running("acme-afterwatch", "task:t2").is_none());
        let mut turn = watched().lock().unwrap().turns.iter().find(|t| t["aid"] == "a1").unwrap().clone();
        assert!(!poll(&mut turn, epoch_s()), "still running");
        std::fs::write(&status, format!("{} 1\n", shell.id())).unwrap();
        assert!(poll(&mut turn, epoch_s()));
        let sent = report(&turn, None);
        assert_eq!(sent["commands"][0]["exit"], 1);
        assert_eq!(sent["commands"][0]["tail"], "3790 passed\n");
        // Past the run's time limit, a command still running is killed, and says so.
        let mut late = turn.clone();
        late["deadline"] = json!(epoch_s() - 1.0);
        late["status_file"] = Value::Null;
        late["commands"][0]["ended"] = Value::Null;
        late["commands"][0]["exit"] = Value::Null;
        assert!(poll(&mut late, epoch_s()));
        assert_eq!(shell.wait().unwrap().signal(), Some(libc::SIGKILL));
        assert_eq!(report(&late, None)["commands"][0]["killed"], true);
        watched().lock().unwrap().turns.retain(|t| t["aid"] != "a1");
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// Gone, or a zombie no one has reaped yet.
    fn gone(pid: u32) -> bool {
        // SAFETY: signal 0 only checks the pid.
        if unsafe { libc::kill(pid as i32, 0) } != 0 {
            return true;
        }
        std::fs::read_to_string(format!("/proc/{pid}/stat")).is_ok_and(|s| s.rsplit_once(')').is_some_and(|(_, r)| r.trim_start().starts_with('Z')))
    }

    #[test]
    fn the_run_limit_ends_a_kept_commands_whole_workload_and_nothing_else() {
        use std::os::unix::process::CommandExt;
        let dir = std::env::temp_dir().join(format!("afterwatch-tree-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let out = dir.join("s1.output");
        // A suite under a shell that waits for it, with a worker under that: no exec anywhere.
        let mut shell = std::process::Command::new("sh")
            .arg("-c")
            .arg("sh -c 'sleep 30; true'; true")
            .stdout(std::fs::File::create(&out).unwrap())
            .process_group(0)
            .spawn()
            .unwrap();
        // Another command's job, in a group of its own.
        let mut other = std::process::Command::new("sleep").arg("30").stdout(std::process::Stdio::null()).process_group(0).spawn().unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        let workload = loop {
            let parent = crate::hosts::claude::parents();
            let under: Vec<u32> = parent.iter().filter(|(_, &p)| p == shell.id() || parent.get(&p) == Some(&shell.id())).map(|(&c, _)| c).collect();
            if under.len() >= 2 || std::time::Instant::now() > deadline {
                break under;
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        assert_eq!(workload.len(), 2, "the suite and its worker");
        let mut turn = json!({"aid": "a2", "deadline": epoch_s() - 1.0, "status_file": null,
                              "commands": [{"task_id": "s1", "description": "Run the full suite", "pid": shell.id(), "output_file": out.display().to_string()}]});
        assert!(poll(&mut turn, epoch_s()));
        assert_eq!(shell.wait().unwrap().signal(), Some(libc::SIGKILL));
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !workload.iter().all(|p| gone(*p)) && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        let left: Vec<&u32> = workload.iter().filter(|p| !gone(**p)).collect();
        let other_alive = !gone(other.id());
        let _ = other.kill();
        let _ = other.wait();
        std::fs::remove_dir_all(dir).unwrap();
        assert!(left.is_empty(), "the suite ran on after its shell was killed: {left:?}");
        assert!(other_alive, "another command's job was killed");
        assert_eq!(report(&turn, None)["commands"][0]["killed"], true);
    }
}
