//! What a Claude or Codex turn leaves running when it ends, found and stopped. A tool command's
//! background job, a dev server, a browser or a busy loop the bot never ended would otherwise run on after the turn,
//! on a machine other bots share.
//!
//! A process is the turn's when any of these holds, from the most exact to the widest:
//! - its environment carries the turn's mark (`MARK_ENV`), which everything the harness starts inherits, a job that
//!   `setsid`s, double-forks or `nohup`s itself included (Linux, where `/proc/<pid>/environ` can be read);
//! - it is in the turn's cgroup: on a cloud machine each harness runs in a scope of its own (crate::turn_limits),
//!   which nothing leaves without writing to the cgroup tree, so this holds even for a process that cleared its
//!   environment;
//! - it is in the process group or session the harness led (hosts::new_group). Neither number is handed to another
//!   process while one member is left; once none is and a new process holds the leader's old pid, the group is not
//!   looked at, so it never names some other turn's processes. On a Mac this is all there is.
//!
//! A process that carries another turn's mark is never this one's, whatever its group or cgroup. Spared: `keep`
//! alone (a reaping parent that stays for kept shells) and `keep_trees` with everything under them (the shells a
//! turn kept on its background wait, which crate::afterwatch watches and ends at the run's time limit). Each is
//! asked to end (SIGTERM), then killed after a short grace; a few passes catch what one forked meanwhile.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// The turn's mark in the harness's environment: the turn's id.
pub const MARK_ENV: &str = "TICO_TURN_MARK";
/// How long the turn's leftovers have to end on a SIGTERM before they are killed.
const GRACE: Duration = Duration::from_millis(if cfg!(test) { 300 } else { 1000 });
/// SIGKILL rounds after the grace: each catches what the last one's processes forked before they died.
const PASSES: usize = 10;
/// How much of a command line a stopped process is reported with.
const COMMAND_CHARS: usize = 120;
/// How many distinct commands the turn's report names.
const TOP: usize = 5;

/// One ended turn: how to know its processes, and what of them stays.
#[derive(Clone, Debug, Default)]
pub struct Turn {
    pub mark: String,
    /// The process groups (and sessions) its harness processes led.
    pub groups: Vec<u32>,
    /// The cgroup directories its harness processes ran in.
    pub cgroups: Vec<PathBuf>,
    /// Processes that stay, alone.
    pub keep: Vec<u32>,
    /// Processes that stay with everything under them.
    pub keep_trees: Vec<u32>,
}

/// A process the sweep stopped.
#[derive(Clone, Debug, PartialEq)]
pub struct Stopped {
    pub pid: u32,
    pub command: String,
}

struct Proc {
    ppid: u32,
    pgid: u32,
    sid: Option<u32>,
    zombie: bool,
    /// The value of its `MARK_ENV`, when its environment can be read and has one.
    mark: Option<String>,
}

/// Every process this runner can see.
#[cfg(target_os = "linux")]
fn table() -> HashMap<u32, Proc> {
    let mut procs = HashMap::new();
    let prefix = format!("{MARK_ENV}=");
    for entry in std::fs::read_dir("/proc").into_iter().flatten().flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        let Ok(stat) = std::fs::read_to_string(entry.path().join("stat")) else {
            continue;
        };
        // After the command's closing paren: state, ppid, pgrp, session.
        let Some((_, rest)) = stat.rsplit_once(')') else {
            continue;
        };
        let fields: Vec<&str> = rest.split_whitespace().collect();
        let num = |i: usize| fields.get(i).and_then(|f| f.parse::<u32>().ok());
        let (Some(ppid), Some(pgid)) = (num(1), num(2)) else {
            continue;
        };
        let mark = std::fs::read(entry.path().join("environ"))
            .ok()
            .and_then(|env| {
                env.split(|b| *b == 0).find_map(|kv| {
                    std::str::from_utf8(kv)
                        .ok()?
                        .strip_prefix(&prefix)
                        .map(String::from)
                })
            });
        procs.insert(
            pid,
            Proc {
                ppid,
                pgid,
                sid: num(3),
                zombie: fields.first().is_some_and(|s| *s == "Z" || *s == "X"),
                mark,
            },
        );
    }
    procs
}

/// Every process this runner can see. No environment: a Mac does not show it to `ps` reliably.
#[cfg(not(target_os = "linux"))]
fn table() -> HashMap<u32, Proc> {
    let mut procs = HashMap::new();
    let Ok(out) = std::process::Command::new("ps")
        .args(["-A", "-o", "pid=,ppid=,pgid=,stat="])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
    else {
        return procs;
    };
    for line in String::from_utf8_lossy(&out.stdout).lines() {
        let cols: Vec<&str> = line.split_whitespace().collect();
        let num = |i: usize| cols.get(i).and_then(|c| c.parse::<u32>().ok());
        if let (Some(pid), Some(ppid), Some(pgid)) = (num(0), num(1), num(2)) {
            procs.insert(
                pid,
                Proc {
                    ppid,
                    pgid,
                    sid: None,
                    zombie: cols.get(3).is_some_and(|s| s.starts_with('Z')),
                    mark: None,
                },
            );
        }
    }
    procs
}

/// The pids in a cgroup and the cgroups under it.
fn cgroup_pids(dir: &std::path::Path, into: &mut HashSet<u32>) {
    if let Ok(text) = std::fs::read_to_string(dir.join("cgroup.procs")) {
        into.extend(text.lines().filter_map(|l| l.trim().parse::<u32>().ok()));
    }
    for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            cgroup_pids(&entry.path(), into);
        }
    }
}

/// A process's command line, one line, bounded.
fn command_of(pid: u32) -> String {
    #[cfg(target_os = "linux")]
    let line = std::fs::read(format!("/proc/{pid}/cmdline"))
        .ok()
        .map(|raw| {
            raw.split(|b| *b == 0)
                .filter(|a| !a.is_empty())
                .map(|a| String::from_utf8_lossy(a).into_owned())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .filter(|l| !l.is_empty())
        .or_else(|| {
            std::fs::read_to_string(format!("/proc/{pid}/comm"))
                .ok()
                .map(|c| c.trim().to_string())
        });
    #[cfg(not(target_os = "linux"))]
    let line = std::process::Command::new("ps")
        .args(["-ww", "-o", "command=", "-p", &pid.to_string()])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    clip(&line.unwrap_or_default(), COMMAND_CHARS)
}

fn clip(text: &str, chars: usize) -> String {
    let line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= chars {
        line
    } else {
        format!("{}…", line.chars().take(chars - 1).collect::<String>())
    }
}

/// The turn's processes still running, spared ones aside.
fn leftovers(turn: &Turn) -> Vec<u32> {
    let procs = table();
    let mut cgrouped = HashSet::new();
    for dir in &turn.cgroups {
        cgroup_pids(dir, &mut cgrouped);
    }
    let me = std::process::id();
    // A group's number stays its own while one member is left. Once none is, it can go to a new process, which may
    // lead a group of its own (another turn's harness): a group whose leader's pid is taken by some process other than
    // a spared one names no leftover of this turn.
    let groups: Vec<u32> = turn
        .groups
        .iter()
        .copied()
        .filter(|g| turn.keep.contains(g) || !procs.contains_key(g))
        .collect();
    let spared_tree = |pid: u32| {
        let mut at = pid;
        // Bounded: a pid table read mid-change can hold a cycle.
        for _ in 0..procs.len().min(4096) {
            if turn.keep_trees.contains(&at) {
                return true;
            }
            match procs.get(&at) {
                Some(p) if p.ppid > 1 && p.ppid != at => at = p.ppid,
                _ => return false,
            }
        }
        false
    };
    let mut found: Vec<u32> = procs
        .iter()
        .filter(|(&pid, p)| {
            if pid <= 1 || pid == me || p.zombie || turn.keep.contains(&pid) {
                return false;
            }
            let ours = match &p.mark {
                Some(mark) => !turn.mark.is_empty() && *mark == turn.mark,
                None => {
                    cgrouped.contains(&pid)
                        || groups.iter().any(|&g| p.pgid == g || p.sid == Some(g))
                }
            };
            ours && !spared_tree(pid)
        })
        .map(|(&pid, _)| pid)
        .collect();
    found.sort_unstable();
    found
}

/// Stops what `turn` left running and returns what it stopped. Blocking: up to about `GRACE` and a few short passes.
pub fn sweep(turn: &Turn) -> Vec<Stopped> {
    let mut stopped: Vec<Stopped> = vec![];
    let mut seen: HashSet<u32> = HashSet::new();
    let mut note = |pid: u32, stopped: &mut Vec<Stopped>| {
        if seen.insert(pid) {
            stopped.push(Stopped {
                pid,
                command: command_of(pid),
            });
        }
    };
    let first = leftovers(turn);
    if first.is_empty() {
        return stopped;
    }
    for &pid in &first {
        note(pid, &mut stopped);
        // SAFETY: a process of the ended turn, found a moment ago.
        unsafe { libc::kill(pid as i32, libc::SIGTERM) };
    }
    let until = Instant::now() + GRACE;
    while Instant::now() < until {
        std::thread::sleep(Duration::from_millis(50));
        if leftovers(turn).is_empty() {
            return stopped;
        }
    }
    for _ in 0..PASSES {
        let left = leftovers(turn);
        if left.is_empty() {
            break;
        }
        for pid in left {
            note(pid, &mut stopped);
            // SAFETY: as above; it outlived its SIGTERM.
            unsafe { libc::kill(pid as i32, libc::SIGKILL) };
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    stopped
}

/// Stop the leftovers off the async runtime and prepare the diagnostic for the attempt's redactor.
pub async fn finish(target: Turn, host: &str) -> Option<Value> {
    let stopped = tokio::task::spawn_blocking(move || sweep(&target))
        .await
        .ok()?;
    if stopped.is_empty() {
        return None;
    }
    crate::util::log(&format!(
        "{host}: turn ended: stopped {} processes it left running ({})",
        stopped.len(),
        programs(&stopped)
    ));
    Some(report(&stopped))
}

/// What the turn's diagnostics say of a sweep: how many it stopped and its most common commands, each cut short.
/// The runner scrubs the run's secrets out of the event.
pub fn report(stopped: &[Stopped]) -> Value {
    let mut counts: Vec<(String, usize)> = vec![];
    for s in stopped {
        let command = clip(&s.command, 80);
        match counts.iter_mut().find(|(c, _)| *c == command) {
            Some((_, n)) => *n += 1,
            None => counts.push((command, 1)),
        }
    }
    // Stable: equal counts keep the order they were found in.
    counts.sort_by_key(|c| std::cmp::Reverse(c.1));
    let top: Vec<Value> = counts
        .iter()
        .take(TOP)
        .map(|(c, n)| json!({"command": c, "count": n}))
        .collect();
    let named = counts
        .iter()
        .take(TOP)
        .map(|(c, n)| format!("{} ({n})", if c.is_empty() { "?" } else { c }))
        .collect::<Vec<_>>()
        .join("; ");
    let n = stopped.len();
    json!({
        "text": format!("Turn ended: stopped {n} process{} it left running: {named}.", if n == 1 { "" } else { "es" }),
        "leftover_processes": {"count": n, "top": top},
    })
}

/// The program names alone, for the runner's own log, which no redactor reads.
pub fn programs(stopped: &[Stopped]) -> String {
    let mut names: Vec<String> = vec![];
    for s in stopped {
        let first = s.command.split_whitespace().next().unwrap_or("?");
        let name = first.rsplit('/').next().unwrap_or(first).to_string();
        if !names.contains(&name) {
            names.push(name);
        }
    }
    names.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn alive(pid: u32) -> bool {
        let Some(p) = table().remove(&pid) else {
            return false;
        };
        !p.zombie
    }

    fn read_pid(path: &std::path::Path) -> Option<u32> {
        std::fs::read_to_string(path).ok()?.trim().parse().ok()
    }

    fn wait_for(paths: &[std::path::PathBuf]) -> Vec<u32> {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let pids: Vec<Option<u32>> = paths.iter().map(|p| read_pid(p)).collect();
            if pids.iter().all(Option::is_some) || Instant::now() > deadline {
                return pids.into_iter().map(|p| p.expect("a pid file")).collect();
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// A long-lived harness with a tool server it started before the turn, and the turn's own processes: a background
    /// job, one that left the session (Linux: `setsid`), and one that double-forked away. After the sweep the turn's
    /// are gone and the harness, its tool server and a process of another turn are not.
    #[test]
    fn a_turns_leftovers_go_and_the_harness_its_tool_server_and_other_turns_stay() {
        let dir =
            std::env::temp_dir().join(format!("turn-sweep-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let setsid = if cfg!(target_os = "linux") {
            "setsid sh -c 'echo $$ > detached.pid; exec sleep 900' </dev/null >/dev/null 2>&1 &\n"
        } else {
            "echo 0 > detached.pid\n"
        };
        let harness = dir.join("harness");
        crate::util::write_script(
            &harness,
            &format!(
                "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > mcp.pid\n\
                 sh -c 'while :; do sleep 1; done' </dev/null >/dev/null 2>&1 &\necho $! > loop.pid\n\
                 {setsid}( sleep 900 </dev/null >/dev/null 2>&1 & echo $! > forked.pid )\n\
                 exec sleep 900\n"
            ),
        );
        let mark = uuid::Uuid::new_v4().to_string();
        let mut child = {
            use std::os::unix::process::CommandExt;
            let mut c = std::process::Command::new(&harness);
            c.current_dir(&dir)
                .env(MARK_ENV, &mark)
                .stdin(std::process::Stdio::null());
            // SAFETY: setsid in a just-forked child, as hosts::new_group.
            unsafe {
                c.pre_exec(|| {
                    libc::setsid();
                    Ok(())
                })
            };
            c.spawn().unwrap()
        };
        // Another turn's process, in no group of this one: it carries a mark of its own.
        let mut other = std::process::Command::new("sleep")
            .arg("900")
            .env(MARK_ENV, "another-turn")
            .spawn()
            .unwrap();
        let names = ["mcp.pid", "loop.pid", "detached.pid", "forked.pid"];
        let pids = wait_for(&names.map(|n| dir.join(n)));
        let (mcp, job, detached, forked) = (pids[0], pids[1], pids[2], pids[3]);
        let harness_pid = child.id();
        let turn = Turn {
            mark: mark.clone(),
            groups: vec![harness_pid],
            cgroups: vec![],
            keep: vec![harness_pid, mcp],
            keep_trees: vec![],
        };
        let stopped = sweep(&turn);
        let gone: Vec<u32> = stopped.iter().map(|s| s.pid).collect();
        let survivors = (
            alive(harness_pid),
            alive(mcp),
            alive(other.id()),
            alive(job),
            alive(forked),
            detached > 0 && alive(detached),
        );
        let _ = other.kill();
        let _ = other.wait();
        // SAFETY: the group this test's harness leads.
        unsafe { libc::kill(-(harness_pid as i32), libc::SIGKILL) };
        let _ = child.wait();
        for pid in [mcp, job, detached, forked].into_iter().filter(|p| *p > 0) {
            // SAFETY: processes this test's harness started.
            unsafe { libc::kill(pid as i32, libc::SIGKILL) };
        }
        std::fs::remove_dir_all(&dir).unwrap();
        let (harness_up, mcp_up, other_up, job_up, forked_up, detached_up) = survivors;
        assert!(harness_up, "the harness was stopped");
        assert!(mcp_up, "the harness's tool server was stopped");
        assert!(other_up, "another turn's process was stopped");
        assert!(!job_up, "the turn's background job outlived it");
        assert!(!forked_up, "a double-forked job outlived the turn");
        assert!(!detached_up, "a setsid job outlived the turn");
        assert!(gone.contains(&job) && gone.contains(&forked), "{stopped:?}");
        if cfg!(target_os = "linux") {
            assert!(gone.contains(&detached), "{stopped:?}");
        }
        let report = report(&stopped);
        assert_eq!(report["leftover_processes"]["count"], stopped.len());
        assert!(
            report["text"].as_str().unwrap().contains("sleep 900"),
            "{report}"
        );
    }

    #[tokio::test]
    async fn codex_sweeps_and_reports_leftovers_after_reaping_its_app_server() {
        let dir = std::env::temp_dir().join(format!("turn-sweep-codex-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let pids_file = dir.join("pids.json");
        crate::util::write_script(
            &dir.join("codex"),
            r#"#!/usr/bin/env python3
import json, os, subprocess, sys
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    result = {}
    if msg['method'] == 'thread/start':
        # Apply the shell's explicit environment, without inheriting the app-server's mark.
        env = msg['params']['config']['shell_environment_policy']['set']
        assert env['TICO_TURN_MARK'] == os.environ['TICO_TURN_MARK'] != 'old-mark'
        pids = [os.getpid()]
        for detached in ([False, True] if sys.platform == 'linux' else [False]):
            child = subprocess.Popen(['/bin/sleep', '900'], env=env, start_new_session=detached,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pids.append(child.pid)
        with open(os.environ['FAKE_PIDS'], 'w') as f:
            json.dump(pids, f)
        result = {'thread': {'id': 'th'}}
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
"#,
        );
        let env: crate::env::Env = [
            (
                "PATH".into(),
                format!("{}:{}", dir.display(), std::env::var("PATH").unwrap()),
            ),
            ("FAKE_PIDS".into(), pids_file.display().to_string()),
            (MARK_ENV.into(), "old-mark".into()),
        ]
        .into();
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let codex = crate::hosts::codex::CodexHost::on_home(env.clone(), tx, None);
        let host = crate::hosts::Host::Codex(codex.clone());
        host.set_attempt("attempt");
        host.start().await.unwrap();
        host.start_thread(
            "ops",
            &crate::hosts::Settings {
                env,
                ..Default::default()
            },
        )
        .await
        .unwrap();
        let pids: Vec<u32> = serde_json::from_slice(&std::fs::read(pids_file).unwrap()).unwrap();
        let report = codex.finish_turn().await;
        let survivors: Vec<u32> = pids.iter().copied().filter(|&pid| alive(pid)).collect();
        for &pid in pids
            .iter()
            .filter(|pid| report.is_none() || survivors.contains(pid))
        {
            // SAFETY: the test's app-server or one of its test jobs, even if the process table cannot see them.
            unsafe { libc::kill(pid as i32, libc::SIGKILL) };
        }
        std::fs::remove_dir_all(dir).unwrap();
        assert!(survivors.is_empty(), "processes survived: {survivors:?}");
        let report = report.expect("a cleanup diagnostic");
        assert_eq!(report["leftover_processes"]["count"], pids.len() - 1);
        assert_eq!(
            report["leftover_processes"]["top"],
            json!([{"command": "/bin/sleep 900", "count": pids.len() - 1}])
        );
        assert!(codex.finish_turn().await.is_none());
    }

    /// A kept shell stays with what it runs; the reaping parent stays alone.
    #[test]
    fn a_kept_tree_stays_whole() {
        use std::os::unix::process::CommandExt;

        let dir =
            std::env::temp_dir().join(format!("turn-sweep-keep-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let shell = dir.join("shell");
        crate::util::write_script(
            &shell,
            "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > worker.pid\nwhile :; do sleep 1; done\n",
        );
        let mark = uuid::Uuid::new_v4().to_string();
        let mut kept = std::process::Command::new(&shell)
            .current_dir(&dir)
            .env(MARK_ENV, &mark)
            .process_group(0)
            .spawn()
            .unwrap();
        let mut stray = std::process::Command::new("sleep")
            .arg("900")
            .env(MARK_ENV, &mark)
            .process_group(kept.id() as i32)
            .spawn()
            .unwrap();
        let worker = wait_for(&[dir.join("worker.pid")])[0];
        let stopped = sweep(&Turn {
            mark,
            groups: vec![kept.id()],
            keep: vec![kept.id()],
            keep_trees: vec![kept.id()],
            ..Default::default()
        });
        let _ = stray.kill();
        let _ = stray.wait();
        let (kept_up, worker_up) = (alive(kept.id()), alive(worker));
        let _ = kept.kill();
        let _ = kept.wait();
        // SAFETY: the kept shell's own job.
        unsafe { libc::kill(worker as i32, libc::SIGKILL) };
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(
            kept_up && worker_up,
            "the kept shell or its job was stopped"
        );
        assert_eq!(
            stopped.iter().map(|s| s.pid).collect::<Vec<_>>(),
            vec![stray.id()]
        );
    }

    /// The turn's harness is gone and its pid now belongs to another turn's harness, which leads a group of its own
    /// under that number: nothing of that group is this turn's.
    #[test]
    fn a_group_whose_leaders_pid_went_to_another_process_is_left_alone() {
        let dir = std::env::temp_dir().join(format!(
            "turn-sweep-reuse-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let harness = dir.join("harness");
        crate::util::write_script(
            &harness,
            "#!/bin/sh\nsleep 900 </dev/null >/dev/null 2>&1 &\necho $! > tool.pid\nexec sleep 900\n",
        );
        let mut other = {
            use std::os::unix::process::CommandExt;
            let mut c = std::process::Command::new(&harness);
            c.current_dir(&dir).stdin(std::process::Stdio::null());
            // SAFETY: setsid in a just-forked child, as hosts::new_group.
            unsafe {
                c.pre_exec(|| {
                    libc::setsid();
                    Ok(())
                })
            };
            c.spawn().unwrap()
        };
        let tool = wait_for(&[dir.join("tool.pid")])[0];
        let stopped = sweep(&Turn {
            mark: uuid::Uuid::new_v4().to_string(),
            groups: vec![other.id()],
            ..Default::default()
        });
        let (leader_up, tool_up) = (alive(other.id()), alive(tool));
        // SAFETY: the group this test's harness leads.
        unsafe { libc::kill(-(other.id() as i32), libc::SIGKILL) };
        let _ = other.wait();
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(stopped.is_empty(), "{stopped:?}");
        assert!(leader_up && tool_up, "another turn's group was stopped");
    }

    #[test]
    fn the_report_counts_and_cuts_commands_and_the_log_names_programs_only() {
        let stopped: Vec<Stopped> = (0..18)
            .map(|i| Stopped {
                pid: 100 + i,
                command: "sh -c while :; do :; done".into(),
            })
            .chain([Stopped {
                pid: 7,
                command: format!("/usr/bin/node server.js --token {}", "x".repeat(200)),
            }])
            .collect();
        let r = report(&stopped);
        assert_eq!(r["leftover_processes"]["count"], 19);
        assert_eq!(
            r["leftover_processes"]["top"][0],
            json!({"command": "sh -c while :; do :; done", "count": 18})
        );
        assert!(
            r["leftover_processes"]["top"][1]["command"]
                .as_str()
                .unwrap()
                .chars()
                .count()
                <= 80
        );
        assert!(r["text"].as_str().unwrap().starts_with(
            "Turn ended: stopped 19 processes it left running: sh -c while :; do :; done (18);"
        ));
        assert_eq!(programs(&stopped), "sh, node");
    }
}
