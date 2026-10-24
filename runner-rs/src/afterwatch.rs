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

/// One turn's kept commands: `{aid, bot, session, status_file, deadline, secrets, commands: [{task_id, description,
/// command, ci_watch, pid, output_file, kept_at, exit, killed, ended}]}`. `ended` is set once the command is over.
/// `secrets`: the turn held values to hide, so its descriptions and commands are stored already scrubbed and its
/// output is only ever sent through its redactor (withheld once a restart has lost it).
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
            // Stored scrubbed: background.json outlives the turn's redactor.
            if let Some(r) = redactor {
                for field in ["description", "command"] {
                    if let Some(t) = c[field].as_str() {
                        c[field] = json!(r.scrub_text(t));
                    }
                }
            }
            c
        })
        .collect();
    if commands.is_empty() {
        return;
    }
    let mut w = watched().lock().unwrap();
    w.turns.push(json!({"aid": aid, "bot": bot, "session": session, "status_file": event["status_file"], "deadline": deadline, "secrets": redactor.is_some(), "commands": commands}));
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

/// Said in place of a command's output when it cannot be scrubbed.
const WITHHELD: &str = "[output withheld: the runner restarted while this command ran, and no longer holds the run's secrets to redact it]";

/// What the wake says about one turn's commands. A turn that held secrets (or one stored before `secrets` was
/// recorded) and whose redactor this runner no longer has, after a restart, gets no output at all: fail closed.
fn report(turn: &Value, redactor: Option<&Arc<Redactor>>) -> Value {
    let blind = redactor.is_none() && turn["secrets"] != false;
    let scrub = |t: &str| redactor.map(|r| r.scrub_text(t)).unwrap_or_else(|| t.to_string());
    let description = |c: &Value| if blind && turn["secrets"].is_null() { UNNAMED.to_string() } else { scrub(&text_of(&c["description"])) };
    let output = |c: &Value| if blind { WITHHELD.to_string() } else { scrub(&tail(&text_of(&c["output_file"]))) };
    let commands: Vec<Value> = turn["commands"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|c| {
            json!({
                "description": description(c).chars().take(300).collect::<String>(),
                "exit": c["exit"],
                "killed": c["killed"] == true,
                "ci_watch": c["ci_watch"],
                "tail": output(c),
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

/// Said in place of a description that was stored before descriptions were stored scrubbed.
const UNNAMED: &str = "A background command";

/// Takes up what an earlier runner left in the state directory. Their redactors went with it. A turn stored before
/// `secrets` was recorded may hold raw text, so it is cleaned before anything reads it (the next turn's prompt, the
/// wake): its descriptions become `UNNAMED`, its commands are dropped, and it counts as holding secrets.
fn restore(dir: &Path) {
    let path: PathBuf = dir.join(FILE);
    let Some(mut turns) = std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Vec<Value>>(&t).ok()) else { return };
    for t in turns.iter_mut().filter(|t| t["secrets"].is_null()) {
        t["secrets"] = json!(true);
        for c in t["commands"].as_array_mut().into_iter().flatten() {
            c["description"] = json!(UNNAMED);
            if let Some(c) = c.as_object_mut() {
                c.remove("command");
            }
        }
    }
    watched().lock().unwrap().turns.extend(turns);
}

/// Watches until the runner stops, starting with what an earlier runner left in the state directory.
pub async fn run_loop(runner: Arc<Runner>) {
    restore(&runner.state.directory);
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

    struct WatchGuard { _one: std::sync::MutexGuard<'static, ()> }
    impl Drop for WatchGuard {
        fn drop(&mut self) {
            let mut w = watched().lock().unwrap();
            w.turns.clear();
            w.redactors.clear();
        }
    }

    /// Each fixture represents a separate runner. A panic must not leave watches for the next fixture to persist
    /// or deliver to its server, including when it simulates a restart.
    fn serial() -> WatchGuard {
        static S: Mutex<()> = Mutex::new(());
        WatchGuard { _one: S.lock().unwrap_or_else(|e| e.into_inner()) }
    }

    struct Child(std::process::Child);
    impl std::ops::Deref for Child {
        type Target = std::process::Child;
        fn deref(&self) -> &Self::Target { &self.0 }
    }
    impl std::ops::DerefMut for Child {
        fn deref_mut(&mut self) -> &mut Self::Target { &mut self.0 }
    }
    impl Drop for Child {
        fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); }
    }

    #[test]
    fn a_kept_command_is_reported_once_it_ends_and_killed_at_the_runs_limit() {
        let _one = serial();
        let dir = std::env::temp_dir().join(format!("afterwatch-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        // Events from lsof name the real path, including /private/var on macOS.
        let dir = std::fs::canonicalize(dir).unwrap();
        let out = dir.join("s1.output");
        // A kept shell: writing to its output file, its exit code to come from its reaping parent's status file.
        let mut shell = Child(std::process::Command::new("sh").arg("-c").arg("echo '3790 passed'; exec sleep 30").stdout(std::fs::File::create(&out).unwrap()).spawn().unwrap());
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
        // A live pid alone is not authority to kill: it must still hold this command's output file.
        let mut different = late.clone();
        different["commands"][0]["output_file"] = json!(dir.join("another.output"));
        assert!(poll(&mut different, epoch_s()));
        assert!(different["commands"][0]["killed"].is_null());
        assert!(shell.try_wait().unwrap().is_none(), "a different process was killed");
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
        #[cfg(target_os = "linux")]
        { std::fs::read_to_string(format!("/proc/{pid}/stat")).is_ok_and(|s| s.rsplit_once(')').is_some_and(|(_, r)| r.trim_start().starts_with('Z'))) }
        #[cfg(not(target_os = "linux"))]
        { std::process::Command::new("ps").args(["-p", &pid.to_string(), "-o", "stat="]).output().is_ok_and(|o| String::from_utf8_lossy(&o.stdout).trim_start().starts_with('Z')) }
    }

    #[test]
    fn the_run_limit_ends_a_kept_commands_whole_workload_and_nothing_else() {
        use std::os::unix::process::CommandExt;
        let dir = std::env::temp_dir().join(format!("afterwatch-tree-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let dir = std::fs::canonicalize(dir).unwrap();
        let out = dir.join("s1.output");
        // A suite under a shell that waits for it, with a worker under that: no exec anywhere.
        let mut shell = Child(std::process::Command::new("sh")
            .arg("-c")
            .arg("sh -c 'sleep 30; true'; true")
            .stdout(std::fs::File::create(&out).unwrap())
            .process_group(0)
            .spawn()
            .unwrap());
        // Another command's job, in a group of its own.
        let mut other = Child(std::process::Command::new("sleep").arg("30").stdout(std::process::Stdio::null()).process_group(0).spawn().unwrap());
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

    const MARKER: &str = "SYNTH_SECRET_9f3a";

    /// A runner whose state directory holds one kept, already ended command that printed `MARKER`, its description
    /// naming it too. Keeps it with the turn's redactor, then (`restart`) drops everything held in memory and takes
    /// the list up again from background.json, as a new runner would. Returns each POST the server saw over two ticks.
    async fn deliver(restart: bool) -> Vec<String> {
        let _one = serial();
        let (url, seen) = crate::client::tests::serve(vec![(200, r#"{"woke": true}"#), (200, r#"{"woke": false}"#)]).await;
        let dir = std::env::temp_dir().join(format!("afterwatch-redact-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("projects")).unwrap();
        let path = dir.join("runner.json");
        std::fs::write(&path, json!({"url": url, "token": "t", "runner_id": "r1", "projects_dir": dir.join("projects")}).to_string()).unwrap();
        let config = crate::config::Config::load(&path).unwrap();
        let mut client = crate::client::Client::new(&config.url(), &config.token(), 5, 0).unwrap();
        client.backoff_scale = 0.0;
        let runner = Runner::new(config, client, crate::state::State::open(&dir.join("state")).unwrap());
        let state = runner.state.directory.clone();
        let out = state.join("s1.output");
        let mut shell = std::process::Command::new("sh").arg("-c").arg(format!("echo 'token={MARKER}'; echo '12 passed'")).stdout(std::fs::File::create(&out).unwrap()).spawn().unwrap();
        shell.wait().unwrap();
        let status = state.join("status");
        std::fs::write(&status, format!("{} 0\n", shell.id())).unwrap();
        let aid = format!("a-{}", uuid::Uuid::new_v4().simple());
        let event = json!({"kept": [{"task_id": "s1", "description": format!("Deploy with {MARKER}"), "command": format!("deploy --key {MARKER}"),
                                     "pid": shell.id(), "output_file": out.display().to_string()}], "status_file": status});
        let redactor = Arc::new(Redactor::new(&[MARKER.to_string()]));
        keep(&state, &aid, "acme-afterwatch", "task:t9", epoch_s() + 3600.0, &event, Some(&redactor));
        let stored = std::fs::read_to_string(state.join(FILE)).unwrap();
        assert!(!stored.contains(MARKER), "a granted value was written to the watch list: {stored}");
        if restart {
            {
                let mut w = watched().lock().unwrap();
                w.turns.retain(|t| t["aid"] != aid.as_str());
                w.redactors.remove(&aid);
            }
            restore(&state);
        }
        tick(&runner).await;
        tick(&runner).await;
        assert!(!watched().lock().unwrap().turns.iter().any(|t| t["aid"] == aid.as_str()), "the turn is done with");
        assert!(!state.join(FILE).exists());
        std::fs::remove_dir_all(dir).ok();
        let seen = seen.lock().unwrap().clone();
        seen.into_iter().filter(|r| r.starts_with(&format!("POST /api/v2/attempts/{aid}/background-done"))).collect()
    }

    fn body_of(request: &str) -> Value {
        serde_json::from_str(&request[request.find("\r\n\r\n").unwrap() + 4..]).unwrap()
    }

    #[tokio::test]
    async fn after_a_restart_a_turn_that_held_secrets_gets_one_wake_with_its_output_withheld() {
        let posts = deliver(true).await;
        assert_eq!(posts.len(), 1, "one wake for the turn: {posts:?}");
        assert!(!posts[0].contains(MARKER), "a granted value reached the server: {}", posts[0]);
        let c = &body_of(&posts[0])["commands"][0];
        assert_eq!(c["exit"], 0);
        assert_eq!(c["tail"], WITHHELD);
        assert_eq!(c["description"], format!("Deploy with {}", crate::redact::MASK));
    }

    #[tokio::test]
    async fn without_a_restart_the_output_is_sent_scrubbed() {
        let posts = deliver(false).await;
        assert_eq!(posts.len(), 1, "one wake for the turn: {posts:?}");
        assert!(!posts[0].contains(MARKER), "a granted value reached the server: {}", posts[0]);
        let c = &body_of(&posts[0])["commands"][0];
        assert_eq!(c["exit"], 0);
        assert_eq!(c["tail"], format!("token={}\n12 passed\n", crate::redact::MASK));
        assert_eq!(c["description"], format!("Deploy with {}", crate::redact::MASK));
    }

    #[test]
    fn a_restored_legacy_watch_names_no_raw_description_in_the_next_turns_prompt() {
        let _one = serial();
        let dir = std::env::temp_dir().join(format!("afterwatch-legacy-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let aid = format!("a-{}", uuid::Uuid::new_v4().simple());
        // Stored by an earlier runner, before `secrets` was recorded: raw description and command, still running.
        let legacy = json!([{"aid": aid, "bot": "acme-legacy", "session": "task:t7", "status_file": null, "deadline": epoch_s() + 3600.0,
                             "commands": [{"task_id": "s1", "description": format!("Deploy with {MARKER}"), "command": format!("deploy --key {MARKER}"),
                                           "pid": 0, "output_file": "/nonexistent", "kept_at": epoch_s()}]}]);
        std::fs::write(dir.join(FILE), legacy.to_string()).unwrap();
        watched().lock().unwrap().redactors.remove(&aid);
        restore(&dir);
        let line = still_running("acme-legacy", "task:t7").expect("the reminder for the next turn");
        let held = watched().lock().unwrap().turns.iter().find(|t| t["aid"] == aid.as_str()).cloned().unwrap();
        watched().lock().unwrap().turns.retain(|t| t["aid"] != aid.as_str());
        std::fs::remove_dir_all(dir).ok();
        assert!(!line.contains(MARKER), "a raw description reached the prompt: {line}");
        assert!(line.contains(&format!("\"{UNNAMED}\"")) && line.contains("Do not start them again"), "{line}");
        assert!(!held.to_string().contains(MARKER), "{held}");
        assert_eq!(report(&held, None)["commands"][0]["tail"], WITHHELD);
    }

    #[test]
    fn a_turn_stored_before_secrets_were_recorded_says_nothing_it_cannot_scrub() {
        let turn = json!({"aid": "a3", "commands": [{"description": format!("Deploy with {MARKER}"), "exit": 0, "output_file": "/nonexistent"}]});
        let sent = report(&turn, None);
        assert!(!sent.to_string().contains(MARKER), "{sent}");
        assert_eq!(sent["commands"][0]["tail"], WITHHELD);
    }
}
