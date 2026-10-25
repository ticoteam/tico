//! Performance diagnostics. While the server says they are on for this computer (the heartbeat reply's
//! `performance`; on unless a person turned them off), the runner reads CPU, load, memory, swap and free disk every
//! 60 s, plus the busiest process names, and posts them in batches to `runners/performance` (backend/performance.py).
//! Off, it reads nothing. Which turns ran at the time is the server's own record.
//!
//! A sample holds numbers and process names (the executable's name) only: never command lines, arguments,
//! environment or file contents.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::runner::Runner;
use crate::util::log;

pub const EVERY: Duration = Duration::from_secs(60);
/// Samples sent together: one post a minute.
const BATCH: usize = 1;
/// Samples kept while the server cannot be reached: ten minutes, then the oldest go.
const KEEP: usize = 10;
/// An older server has no `runners/performance`: asked again after this long.
const MISSING_BACKOFF: Duration = Duration::from_secs(600);
/// Process names a sample carries: the busiest by CPU, then by memory.
const TOP_CPU: usize = 8;
const TOP_MEM: usize = 4;

static ENABLED: AtomicBool = AtomicBool::new(false);

/// Turned on or off by the heartbeat reply.
pub fn set_enabled(on: bool) {
    if ENABLED.swap(on, Ordering::SeqCst) != on {
        log(if on { "Tico runner: performance diagnostics on" } else { "Tico runner: performance diagnostics off" });
    }
}

pub fn enabled() -> bool {
    ENABLED.load(Ordering::SeqCst)
}

#[derive(Clone, Debug, Default)]
struct Proc {
    pid: u32,
    ppid: u32,
    /// CPU time used so far, user and system, in milliseconds.
    cpu_ms: u64,
    rss: u64,
    name: String,
    start: u64,
}

#[derive(Default)]
struct Memory {
    total: u64,
    available: u64,
    swap_total: u64,
    swap_used: u64,
}

/// One sampler's memory of the last reading, so CPU is a rate over the interval.
#[derive(Default)]
pub struct Sampler {
    at: Option<Instant>,
    procs: HashMap<u32, (u64, u64)>,
    cpu_total: Option<(u64, u64)>,
    cgroup_usage: Option<u64>,
}

impl Sampler {
    /// One sample, or None on the first call (it only sets the baseline the next one measures from).
    pub fn sample(&mut self, active: usize, volumes: &[(&str, PathBuf)]) -> Option<Value> {
        let now = Instant::now();
        let procs = list_procs();
        let elapsed_ms = self.at.map(|a| now.duration_since(a).as_millis() as u64).unwrap_or(0);
        let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1) as f64;
        let previous = std::mem::take(&mut self.procs);
        self.procs = procs.iter().map(|p| (p.pid, (p.start, p.cpu_ms))).collect();
        let first = self.at.is_none();
        self.at = Some(now);
        let cpu_total = system_cpu();
        let previous_total = std::mem::replace(&mut self.cpu_total, cpu_total);
        let usage = cgroup_cpu_usage();
        let previous_usage = std::mem::replace(&mut self.cgroup_usage, usage);
        if first || elapsed_ms == 0 {
            return None;
        }
        // Each process's CPU over the interval, in percent of one core.
        let rates: Vec<f64> = procs
            .iter()
            .map(|p| {
                let before = previous.get(&p.pid).filter(|(start, _)| *start == p.start).map(|(_, ms)| *ms).unwrap_or(0);
                let used = p.cpu_ms.saturating_sub(before) as f64;
                (100.0 * used / elapsed_ms as f64).min(100.0 * cores)
            })
            .collect();
        let mut cpu = match (previous_total, cpu_total) {
            (Some((b0, t0)), Some((b1, t1))) if t1 > t0 => 100.0 * (b1.saturating_sub(b0)) as f64 / (t1 - t0) as f64,
            _ => rates.iter().sum::<f64>() / cores,
        };
        let mut memory = memory();
        let (mut scope, mut memory_scope) = ("host", "host");
        if crate::follow::kind() == "docker" {
            // A container's limits are what it runs out of, not the host's.
            if let Some((used, limit)) = cgroup_memory().filter(|(_, limit)| memory.total == 0 || *limit < memory.total) {
                memory.total = limit;
                memory.available = limit.saturating_sub(used);
                (scope, memory_scope) = ("container", "container");
            }
            // The container's own CPU time, over its quota or, without one, every core it can reach.
            if let (Some(u0), Some(u1)) = (previous_usage, usage) {
                let limit = cgroup_cpu_limit().unwrap_or(cores);
                cpu = 100.0 * (u1.saturating_sub(u0) as f64 / 1000.0) / (elapsed_ms as f64 * limit);
                scope = "container";
            }
        }
        let mut sample = Map::new();
        sample.insert("at".into(), json!(unix_now()));
        sample.insert("interval_s".into(), json!(EVERY.as_secs()));
        sample.insert("scope".into(), json!(scope));
        if scope == "container" {
            // Without a memory limit, memory is still the host's.
            sample.insert("memory_scope".into(), json!(memory_scope));
        }
        sample.insert("cpu".into(), json!(round1(cpu.clamp(0.0, 100.0))));
        sample.insert("cores".into(), json!(cores as u64));
        if let Some(load) = load_average() {
            sample.insert("load".into(), json!(load.map(round1)));
        }
        if memory.total > 0 {
            sample.insert("mem_total".into(), json!(memory.total));
            sample.insert("mem_used".into(), json!(memory.total.saturating_sub(memory.available)));
        }
        add_swap(&mut sample, &memory, cfg!(target_os = "linux"));
        let mut disks: Vec<Value> = Vec::new();
        let mut seen = Vec::new();
        for (volume, path) in volumes {
            if let Some((total, free)) = crate::readiness::disk_usage(path) {
                if !seen.contains(&(total, free)) {
                    seen.push((total, free));
                    disks.push(json!({"volume": volume, "total_bytes": total, "free_bytes": free}));
                }
            }
        }
        sample.insert("disk".into(), json!(disks));
        sample.insert("active".into(), json!(active));
        sample.insert("procs".into(), json!(top_names(&procs, &rates)));
        Some(Value::Object(sample))
    }
}

/// macOS grows its swap total with use: report raw bytes, but keep them out of the diagnostic swap rule.
fn add_swap(sample: &mut Map<String, Value>, memory: &Memory, diagnostic: bool) {
    if memory.swap_total > 0 {
        sample.insert("load_swap_total".into(), json!(memory.swap_total));
        sample.insert("load_swap_used".into(), json!(memory.swap_used));
        if diagnostic {
            sample.insert("swap_total".into(), json!(memory.swap_total));
            sample.insert("swap_used".into(), json!(memory.swap_used));
        }
    }
}

/// The busiest process names on the computer, by CPU and then by memory.
fn top_names(procs: &[Proc], rates: &[f64]) -> Vec<Value> {
    let mut by_name: HashMap<String, (f64, u64, u64)> = HashMap::new();
    for (i, p) in procs.iter().enumerate() {
        let name = clean_name(&p.name);
        if name.is_empty() {
            continue;
        }
        let e = by_name.entry(name).or_default();
        e.0 += rates[i];
        e.1 += p.rss;
        e.2 += 1;
    }
    let mut rows: Vec<(String, (f64, u64, u64))> = by_name.into_iter().collect();
    rows.sort_by(|a, b| b.1 .0.total_cmp(&a.1 .0).then_with(|| a.0.cmp(&b.0)));
    let mut picked: Vec<(String, (f64, u64, u64))> = rows.iter().filter(|r| r.1 .0 >= 1.0).take(TOP_CPU).cloned().collect();
    rows.sort_by(|a, b| b.1 .1.cmp(&a.1 .1).then_with(|| a.0.cmp(&b.0)));
    for r in rows.into_iter().take(TOP_MEM) {
        if !picked.iter().any(|p| p.0 == r.0) {
            picked.push(r);
        }
    }
    picked.into_iter().map(|(name, (cpu, mem, count))| json!({"name": name, "cpu": round1(cpu), "mem": mem, "count": count})).collect()
}

/// An executable's name as a sample may carry it: its base name, letters, digits and `._+-`, 40 characters at most.
fn clean_name(name: &str) -> String {
    let base = name.rsplit('/').next().unwrap_or(name);
    base.chars().filter(|c| c.is_ascii_alphanumeric() || "._+-".contains(*c)).take(40).collect()
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

fn unix_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0).floor()
}

fn load_average() -> Option<[f64; 3]> {
    let mut load = [0f64; 3];
    // SAFETY: getloadavg writes at most 3 doubles into the array.
    (unsafe { libc::getloadavg(load.as_mut_ptr(), 3) } == 3).then_some(load)
}

// ------------------------------------------------------------------------------------------------ Linux: /proc

#[cfg(target_os = "linux")]
fn list_procs() -> Vec<Proc> {
    let tick = clock_tick();
    let page = page_size();
    let Ok(dir) = std::fs::read_dir("/proc") else { return Vec::new() };
    dir.flatten()
        .filter_map(|e| e.file_name().to_str().and_then(|n| n.parse::<u32>().ok()))
        .filter_map(|pid| std::fs::read_to_string(format!("/proc/{pid}/stat")).ok().and_then(|s| parse_stat(pid, &s, tick, page)))
        .collect()
}

/// `/proc/<pid>/stat`: the name sits in parentheses and may hold spaces or parentheses itself.
fn parse_stat(pid: u32, stat: &str, tick: u64, page: u64) -> Option<Proc> {
    let open = stat.find('(')?;
    let close = stat.rfind(')')?;
    let name = stat.get(open + 1..close)?.to_string();
    let rest: Vec<&str> = stat.get(close + 1..)?.split_whitespace().collect();
    let num = |i: usize| rest.get(i).and_then(|v| v.parse::<u64>().ok());
    let ticks = num(11)? + num(12)?;
    Some(Proc { pid, ppid: num(1)? as u32, cpu_ms: ticks * 1000 / tick.max(1), rss: num(21)? * page, name, start: num(19)? })
}

#[cfg(target_os = "linux")]
fn clock_tick() -> u64 {
    // SAFETY: sysconf reads a constant.
    let t = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if t > 0 { t as u64 } else { 100 }
}

#[cfg(target_os = "linux")]
fn page_size() -> u64 {
    // SAFETY: sysconf reads a constant.
    let p = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if p > 0 { p as u64 } else { 4096 }
}

/// (busy, total) jiffies over every CPU, from `/proc/stat`'s first line.
#[cfg(target_os = "linux")]
fn system_cpu() -> Option<(u64, u64)> {
    parse_cpu_line(std::fs::read_to_string("/proc/stat").ok()?.lines().next()?)
}

fn parse_cpu_line(line: &str) -> Option<(u64, u64)> {
    let mut parts = line.split_whitespace();
    if parts.next()? != "cpu" {
        return None;
    }
    // user nice system idle iowait irq softirq steal (guest time is already inside user)
    let v: Vec<u64> = parts.take(8).filter_map(|x| x.parse().ok()).collect();
    if v.len() < 4 {
        return None;
    }
    let total: u64 = v.iter().sum();
    let idle = v[3] + v.get(4).copied().unwrap_or(0);
    Some((total - idle, total))
}

#[cfg(target_os = "linux")]
fn memory() -> Memory {
    std::fs::read_to_string("/proc/meminfo").map(|s| parse_meminfo(&s)).unwrap_or_default()
}

fn parse_meminfo(text: &str) -> Memory {
    let field = |name: &str| {
        text.lines()
            .find_map(|l| l.strip_prefix(name).and_then(|r| r.strip_prefix(':')))
            .and_then(|r| r.split_whitespace().next()?.parse::<u64>().ok())
            .map(|kb| kb * 1024)
    };
    let swap_total = field("SwapTotal").unwrap_or(0);
    Memory {
        total: field("MemTotal").unwrap_or(0),
        available: field("MemAvailable").or_else(|| field("MemFree")).unwrap_or(0),
        swap_total,
        swap_used: swap_total.saturating_sub(field("SwapFree").unwrap_or(swap_total)),
    }
}

const CGROUP: &str = "/sys/fs/cgroup";

/// (used, limit) bytes of this container's cgroup (v2), when it has a limit. Used leaves out reclaimable file cache,
/// as `docker stats` does.
fn cgroup_memory() -> Option<(u64, u64)> {
    let read = |f: &str| std::fs::read_to_string(format!("{CGROUP}/{f}")).ok();
    let limit: u64 = read("memory.max")?.trim().parse().ok()?;
    let current: u64 = read("memory.current")?.trim().parse().ok()?;
    let inactive = read("memory.stat")
        .and_then(|s| s.lines().find_map(|l| l.strip_prefix("inactive_file ").and_then(|v| v.trim().parse::<u64>().ok())))
        .unwrap_or(0);
    Some((current.saturating_sub(inactive), limit))
}

/// The cgroup's CPU time so far, in microseconds.
fn cgroup_cpu_usage() -> Option<u64> {
    let stat = std::fs::read_to_string(format!("{CGROUP}/cpu.stat")).ok()?;
    stat.lines().find_map(|l| l.strip_prefix("usage_usec ").and_then(|v| v.trim().parse().ok()))
}

/// How many cores the cgroup may use (`cpu.max` quota over period), when it is limited.
fn cgroup_cpu_limit() -> Option<f64> {
    let max = std::fs::read_to_string(format!("{CGROUP}/cpu.max")).ok()?;
    let mut parts = max.split_whitespace();
    let quota: f64 = parts.next()?.parse().ok()?;
    let period: f64 = parts.next()?.parse().ok()?;
    (period > 0.0 && quota > 0.0).then_some(quota / period)
}

// -------------------------------------------------------------------------------------- macOS: ps, sysctl, vm_stat

#[cfg(not(target_os = "linux"))]
fn list_procs() -> Vec<Proc> {
    let Some(out) = run(&["ps", "-axo", "pid=,ppid=,rss=,time=,comm="]) else { return Vec::new() };
    out.lines().filter_map(parse_ps_line).collect()
}

/// One `ps -o pid=,ppid=,rss=,time=,comm=` line (rss in KB, time `[dd-][hh:]mm:ss[.cc]`).
#[cfg_attr(target_os = "linux", allow(dead_code))]
fn parse_ps_line(line: &str) -> Option<Proc> {
    let mut parts = line.split_whitespace();
    let pid = parts.next()?.parse().ok()?;
    let ppid = parts.next()?.parse().ok()?;
    let rss = parts.next()?.parse::<u64>().ok()? * 1024;
    let cpu_ms = parse_cpu_time(parts.next()?)?;
    let name = parts.collect::<Vec<_>>().join(" ");
    Some(Proc { pid, ppid, cpu_ms, rss, name, start: 0 })
}

#[cfg_attr(target_os = "linux", allow(dead_code))]
fn parse_cpu_time(text: &str) -> Option<u64> {
    let (days, clock) = match text.split_once('-') {
        Some((d, rest)) => (d.parse::<u64>().ok()?, rest),
        None => (0, text),
    };
    let mut seconds = 0f64;
    for part in clock.split(':') {
        seconds = seconds * 60.0 + part.parse::<f64>().ok()?;
    }
    Some(((days as f64 * 86400.0 + seconds) * 1000.0) as u64)
}

/// No cheap whole-computer counter here: CPU is the processes' sum (`Sampler::sample`).
#[cfg(not(target_os = "linux"))]
fn system_cpu() -> Option<(u64, u64)> {
    None
}

#[cfg(not(target_os = "linux"))]
fn memory() -> Memory {
    let total = run(&["sysctl", "-n", "hw.memsize"]).and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let used = run(&["vm_stat"]).map(|s| vm_stat_used(&s)).unwrap_or(0);
    // Raw swap is visible in simple load readings; add_swap keeps it out of diagnostic overloads.
    let (swap_total, swap_used) = run(&["sysctl", "-n", "vm.swapusage"]).map(|s| swap_usage(&s)).unwrap_or((0, 0));
    Memory { total, available: total.saturating_sub(used), swap_total, swap_used }
}

#[cfg_attr(target_os = "linux", allow(dead_code))]
fn swap_usage(text: &str) -> (u64, u64) {
    let bytes = |key: &str| {
        text.split(&format!("{key} = ")).nth(1).and_then(|s| s.split_whitespace().next())
            .and_then(|s| s.strip_suffix('M')).and_then(|s| s.parse::<f64>().ok())
            .map(|n| (n * 1024.0 * 1024.0) as u64).unwrap_or(0)
    };
    (bytes("total"), bytes("used"))
}

/// Activity Monitor's Memory Used from `vm_stat`: app memory (anonymous pages less purgeable ones), wired memory and
/// the compressor's pages. File cache (inactive and active file-backed pages) is not counted, as it can be handed out.
#[cfg_attr(target_os = "linux", allow(dead_code))]
fn vm_stat_used(text: &str) -> u64 {
    let page = text
        .lines()
        .next()
        .and_then(|l| l.split("page size of ").nth(1))
        .and_then(|r| r.split_whitespace().next()?.parse::<u64>().ok())
        .unwrap_or(4096);
    let pages = |name: &str| {
        text.lines()
            .find_map(|l| l.strip_prefix(name))
            .and_then(|r| r.trim_start_matches(':').trim().trim_end_matches('.').parse::<u64>().ok())
            .unwrap_or(0)
    };
    (pages("Anonymous pages").saturating_sub(pages("Pages purgeable")) + pages("Pages wired down")
        + pages("Pages occupied by compressor"))
        * page
}

#[cfg(not(target_os = "linux"))]
fn run(argv: &[&str]) -> Option<String> {
    let out = std::process::Command::new(argv[0]).args(&argv[1..]).stdin(std::process::Stdio::null()).output().ok()?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Instantaneous high-level reading: one-minute load per core, memory, and active turns. No process scan.
pub fn load_snapshot(active: usize) -> Value {
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1) as f64;
    let mut mem = memory();
    if crate::follow::kind() == "docker" {
        if let Some((used, limit)) = cgroup_memory().filter(|(_, limit)| mem.total == 0 || *limit < mem.total) {
            mem.total = limit;
            mem.available = limit.saturating_sub(used);
        }
    }
    json!({"at": unix_now(), "cpu_pct": load_average().map(|l| round1(100.0 * l[0] / cores)),
           "mem_pct": (mem.total > 0).then(|| round1(100.0 * mem.total.saturating_sub(mem.available) as f64 / mem.total as f64)),
           "running_turns": active})
}

/// Add only for servers that accept the field; diagnostics off means no reading.
pub async fn with_snapshot(body: Value, active: usize) -> Value {
    with_snapshot_if(body, active, enabled(), crate::follow::feature("load_snapshot")).await
}

async fn with_snapshot_if(mut body: Value, active: usize, on: bool, supported: bool) -> Value {
    if on && supported {
        if let Ok(snapshot) = tokio::task::spawn_blocking(move || load_snapshot(active)).await {
            body["load_snapshot"] = snapshot;
        }
    }
    body
}

// ------------------------------------------------------------------------------------------------ the loop

/// Samples every 60 s while on, posts a batch each minute. An older server without the endpoint is asked again
/// after ten minutes; a failed post keeps the samples for the next one, ten minutes at most.
pub async fn run_loop(runner: Arc<Runner>) {
    let mut sampler = Sampler::default();
    let mut batch: Vec<Value> = Vec::new();
    let mut missing_until: Option<Instant> = None;
    let mut ticker = tokio::time::interval(EVERY);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    while !runner.stopping() {
        ticker.tick().await;
        if !enabled() || missing_until.is_some_and(|t| Instant::now() < t) {
            sampler = Sampler::default();
            batch.clear();
            continue;
        }
        missing_until = None;
        let active = runner.active.lock().unwrap().len();
        let volumes = vec![("state", runner.state.directory.clone()), ("projects", runner.config.projects_dir())];
        let (back, sample) = match tokio::task::spawn_blocking(move || {
            let s = sampler.sample(active, &volumes);
            (sampler, s)
        })
        .await
        {
            Ok(done) => done,
            Err(_) => (Sampler::default(), None),
        };
        sampler = back;
        let Some(sample) = sample else { continue };
        batch.push(sample);
        if batch.len() > KEEP {
            batch.remove(0);
        }
        if batch.len() < BATCH {
            continue;
        }
        match runner.client.post("runners/performance", &json!({"samples": batch}), None).await {
            Ok(_) => batch.clear(),
            Err(e) if e.status == 404 || e.status == 405 => {
                missing_until = Some(Instant::now() + MISSING_BACKOFF);
                batch.clear();
            }
            Err(e) if e.status == 409 || e.status == 422 => {
                // Turned off since the last heartbeat, or a sample this server cannot read: those samples go.
                log(&format!("Tico runner: performance samples refused ({})", e.status));
                batch.clear();
            }
            Err(_) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(pid: u32, ppid: u32, rss: u64, name: &str) -> Proc {
        Proc { pid, ppid, cpu_ms: 0, rss, name: name.into(), start: 7 }
    }

    #[test]
    fn high_level_snapshot_needs_no_cpu_baseline_and_names_no_processes() {
        let snapshot = load_snapshot(7);
        assert_eq!(snapshot["running_turns"], json!(7));
        assert!(snapshot["at"].as_f64().unwrap() > 0.0);
        assert!(snapshot.get("cpu_pct").is_some() && snapshot.get("mem_pct").is_some());
        assert_eq!(snapshot.as_object().unwrap().len(), 4);
        let mut sampler = Sampler { at: Some(Instant::now() - EVERY), ..Sampler::default() };
        let sample = sampler.sample(7, &[]).unwrap();
        assert_eq!(sample["interval_s"], json!(60));
        assert_eq!(sample["active"], json!(7));
    }

    #[tokio::test]
    async fn snapshots_require_diagnostics_and_server_support() {
        for (on, supported) in [(false, false), (false, true), (true, false)] {
            let body = json!({"thread_id": "gate"});
            assert_eq!(with_snapshot_if(body.clone(), 2, on, supported).await, body);
        }
        let body = with_snapshot_if(json!({"thread_id": "gate"}), 2, true, true).await;
        assert_eq!(body["load_snapshot"]["running_turns"], json!(2));
        assert_eq!(body["thread_id"], json!("gate"));
    }

    #[test]
    fn raw_mac_swap_does_not_enter_diagnostic_fields() {
        let memory = Memory { total: 1000, available: 150, swap_total: 100, swap_used: 95 };
        let mut mac = Map::new();
        add_swap(&mut mac, &memory, false);
        assert_eq!(mac["load_swap_used"], json!(95));
        assert_eq!(mac["load_swap_total"], json!(100));
        assert!(!mac.contains_key("swap_used") && !mac.contains_key("swap_total"));
        let mut linux = Map::new();
        add_swap(&mut linux, &memory, true);
        assert_eq!(linux["swap_used"], json!(95));
        assert_eq!(linux["swap_total"], json!(100));
    }

    #[test]
    fn process_names_are_base_names_and_never_arguments() {
        assert_eq!(clean_name("/usr/local/bin/cargo"), "cargo");
        assert_eq!(clean_name("/Applications/Claude.app/Contents/MacOS/Claude Helper"), "ClaudeHelper");
        let procs = vec![p(1, 0, 10, "cargo"), p(2, 0, 10, "cargo"), p(3, 0, 5000, "postgres"), p(4, 0, 1, "idle")];
        let top = top_names(&procs, &[30.0, 30.0, 0.0, 0.0]);
        assert_eq!(top[0]["name"], "cargo");
        assert_eq!(top[0]["cpu"], json!(60.0));
        assert_eq!(top[0]["count"], json!(2));
        assert!(top.iter().any(|t| t["name"] == "postgres"));
    }

    #[test]
    fn proc_stat_cpu_and_meminfo_parse() {
        let stat = "4242 (my (odd) name) S 1 4242 4242 0 -1 4194560 100 0 0 0 250 50 0 0 20 0 1 0 12345 1000 300 0";
        let proc = parse_stat(4242, stat, 100, 4096).unwrap();
        assert_eq!((proc.name.as_str(), proc.ppid, proc.cpu_ms, proc.rss, proc.start), ("my (odd) name", 1, 3000, 300 * 4096, 12345));
        assert_eq!(parse_cpu_line("cpu  100 0 50 800 50 0 0 0 0 0"), Some((150, 1000)));
        let m = parse_meminfo("MemTotal:  1000 kB\nMemFree: 10 kB\nMemAvailable: 400 kB\nSwapTotal: 100 kB\nSwapFree: 60 kB\n");
        assert_eq!((m.total, m.available, m.swap_total, m.swap_used), (1024000, 409600, 102400, 40960));
    }

    #[test]
    fn mac_ps_and_vm_stat_parse() {
        let proc = parse_ps_line("  812     1   20480   1:02.50 /Applications/Claude.app/Contents/MacOS/Claude Helper").unwrap();
        assert_eq!((proc.pid, proc.ppid, proc.rss, proc.cpu_ms), (812, 1, 20480 * 1024, 62500));
        assert_eq!(parse_cpu_time("1-00:00:01"), Some(86_401_000));
        let vm = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:  10.\nPages active: 99.\nPages inactive: 5.\nPages purgeable: 4.\nPages wired down: 30.\nFile-backed pages: 60.\nAnonymous pages: 50.\nPages occupied by compressor: 6.\n";
        assert_eq!(vm_stat_used(vm), (50 - 4 + 30 + 6) * 16384);
        assert_eq!(swap_usage("total = 1024.00M  used = 256.00M  free = 768.00M (encrypted)"),
                   (1024 * 1024 * 1024, 256 * 1024 * 1024));
    }
}
