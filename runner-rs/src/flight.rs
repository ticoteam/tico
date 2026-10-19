//! The runner's flight file: where a turn's time went, slow heartbeats, slow git commands and a per-minute process
//! sample, as rotating NDJSON in the state directory (`flight.ndjson`, then `.1` to `.4`, 10 MB each). One JSON
//! object per line with `v` (1), `ts` (Unix seconds, milliseconds as decimals) and `kind` (`runner.*`): the server's
//! flight file envelope (`backend/flight_file.py`), so one reader takes both. A turn never waits on the disk: records go through a bounded queue to one writer thread,
//! and a record that finds the queue full is dropped and counted in the next process sample.
//!
//! Records hold timings, counts, bot slugs, attempt ids and git verbs only: never prompts, replies, tokens, command
//! arguments or file contents.

use std::collections::{BTreeMap, HashMap};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

pub const FILE: &str = "flight.ndjson";
const MAX_BYTES: u64 = 10 * 1024 * 1024;
/// The current file plus four rotated ones.
const KEEP: usize = 5;
const QUEUE: usize = 4096;
const SAMPLE_EVERY: Duration = Duration::from_secs(60);
/// A heartbeat round trip at least this long is recorded (failures always are).
pub const HEARTBEAT_SLOW_MS: u64 = 1000;
/// A git command at least this long is recorded (timeouts and commands that never ran always are).
pub const GIT_SLOW_MS: u64 = 2000;
/// The order a turn passes its marks in (`util::mark` names plus the flight-only ones).
const PHASES: [&str; 13] = [
    "claimed", "placed", "harness_started", "harness_spawned", "first_output", "first_delta_posted", "model_done", "scrubbed", "finished", "saved",
    "flushed", "complete_posted", "done",
];

static SENDER: OnceLock<SyncSender<Map<String, Value>>> = OnceLock::new();
static DROPPED: AtomicU64 = AtomicU64::new(0);

struct Turn {
    at: Instant,
    phases: Vec<(String, u64)>,
}

static TURNS: Mutex<Option<HashMap<String, Turn>>> = Mutex::new(None);

/// Start the writer for `state_dir` (the `run` command only). Off when `TICO_FLIGHT_RECORDER=0`, as on the server.
pub fn init(state_dir: &Path) {
    if std::env::var("TICO_FLIGHT_RECORDER").ok().as_deref() == Some("0") || SENDER.get().is_some() {
        return;
    }
    let (tx, rx) = sync_channel::<Map<String, Value>>(QUEUE);
    let mut writer = Writer::new(state_dir, MAX_BYTES);
    let spawned = std::thread::Builder::new().name("flight".into()).spawn(move || {
        let mut next_sample = Instant::now() + SAMPLE_EVERY;
        loop {
            match rx.recv_timeout(next_sample.saturating_duration_since(Instant::now())) {
                Ok(record) => writer.write(&record),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => return,
            }
            if Instant::now() >= next_sample {
                next_sample = Instant::now() + SAMPLE_EVERY;
                writer.write(&envelope("runner.process", process_sample()));
            }
        }
    });
    if spawned.is_ok() {
        let _ = SENDER.set(tx);
        let mut start = Map::new();
        start.insert("version".into(), json!(crate::runner::RUNNER_VERSION));
        start.insert("build".into(), json!(crate::BUILD_ID));
        start.insert("pid".into(), json!(std::process::id()));
        emit("runner.start", start);
    }
}

/// Queue one record; never blocks. Without a writer (other commands, tests) it does nothing.
pub fn emit(kind: &str, fields: Map<String, Value>) {
    let Some(tx) = SENDER.get() else { return };
    if let Err(TrySendError::Full(_)) = tx.try_send(envelope(kind, fields)) {
        DROPPED.fetch_add(1, Ordering::Relaxed);
    }
}

fn envelope(kind: &str, fields: Map<String, Value>) -> Map<String, Value> {
    let mut record = Map::new();
    record.insert("v".into(), json!(1));
    record.insert("ts".into(), json!(now_ts()));
    record.insert("kind".into(), json!(kind));
    record.extend(fields);
    record
}

/// Now as Unix seconds to the millisecond.
fn now_ts() -> f64 {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    now.as_millis() as f64 / 1000.0
}

/// A record's `ts`: Unix seconds; an ISO-8601 string is read too. 0 when missing.
fn record_ts(record: &Value) -> f64 {
    match &record["ts"] {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(t) => crate::util::parse_iso(t),
        _ => 0.0,
    }
}

/// A turn reached `name` (`util::mark` calls this for every mark). `claimed` opens the turn; a later mark for a
/// turn not open is ignored, so nothing builds up for turns this process never claimed.
pub fn phase(name: &str, attempt: &str) {
    if SENDER.get().is_none() || attempt == "-" {
        return;
    }
    let mut turns = TURNS.lock().unwrap_or_else(|p| p.into_inner());
    let turns = turns.get_or_insert_with(HashMap::new);
    if name == "claimed" {
        if turns.len() >= 512 {
            // A worker that died before `turn_end` leaves its entry: the oldest go.
            let oldest = turns.iter().min_by_key(|(_, t)| t.at).map(|(k, _)| k.clone());
            if let Some(k) = oldest {
                turns.remove(&k);
            }
        }
        turns.insert(attempt.to_string(), Turn { at: Instant::now(), phases: vec![("claimed".into(), 0)] });
    } else if let Some(turn) = turns.get_mut(attempt) {
        if !turn.phases.iter().any(|(n, _)| n == name) {
            turn.phases.push((name.to_string(), turn.at.elapsed().as_millis() as u64));
        }
    }
}

/// The turn's end: one `runner.turn` record with every phase's milliseconds since it was claimed.
pub fn turn_end(attempt: &str, bot: &str, runtime: &str, outcome: &str, settled: bool) {
    phase("done", attempt);
    let turn = TURNS.lock().unwrap_or_else(|p| p.into_inner()).as_mut().and_then(|t| t.remove(attempt));
    let Some(turn) = turn else { return };
    let phases: Map<String, Value> = turn.phases.iter().map(|(n, ms)| (n.clone(), json!(ms))).collect();
    let mut f = Map::new();
    f.insert("attempt".into(), json!(attempt));
    f.insert("bot".into(), json!(bot));
    f.insert("runtime".into(), json!(runtime));
    f.insert("outcome".into(), json!(outcome));
    f.insert("settled".into(), json!(settled));
    f.insert("total_ms".into(), json!(turn.at.elapsed().as_millis() as u64));
    f.insert("phases".into(), Value::Object(phases));
    emit("runner.turn", f);
}

/// One heartbeat's round trip (all its tries); recorded when slow or failed.
pub fn heartbeat(ms: u64, tries: u32, error: Option<&crate::client::ApiError>) {
    if ms < HEARTBEAT_SLOW_MS && error.is_none() {
        return;
    }
    let mut f = Map::new();
    f.insert("ms".into(), json!(ms));
    f.insert("tries".into(), json!(tries));
    f.insert("ok".into(), json!(error.is_none()));
    if let Some(e) = error {
        f.insert("status".into(), json!(e.status));
        f.insert("code".into(), json!(e.code));
    }
    emit("runner.heartbeat", f);
}

/// One git command (`gitrun::run`): only the verb and the checkout's folder name, never the arguments (a clone's
/// URL may carry a token). `fault` is `timeout`, `stopped` or `io` when it has no exit status.
pub fn git(argv: &[String], ms: u64, code: Option<i32>, fault: Option<&str>) {
    if ms < GIT_SLOW_MS && fault.is_none_or(|f| f == "stopped") {
        return;
    }
    let (verb, repo) = git_verb(argv);
    let mut f = Map::new();
    f.insert("verb".into(), json!(verb));
    if let Some(r) = repo {
        f.insert("repo".into(), json!(r));
    }
    f.insert("ms".into(), json!(ms));
    f.insert("code".into(), code.map_or(Value::Null, |c| json!(c)));
    if let Some(fault) = fault {
        f.insert("fault".into(), json!(fault));
    }
    emit("runner.git", f);
}

/// The subcommand of a git argv, past its `-c`/`-C` options, and the `-C` folder's last name.
fn git_verb(argv: &[String]) -> (String, Option<String>) {
    let mut repo = None;
    let mut i = 1;
    while i < argv.len() {
        let a = &argv[i];
        if a == "-c" || a == "-C" {
            if a == "-C" {
                repo = argv.get(i + 1).and_then(|p| Path::new(p).file_name()).map(|n| n.to_string_lossy().into_owned());
            }
            i += 2;
        } else if a.starts_with('-') {
            i += 1;
        } else {
            return (a.clone(), repo);
        }
    }
    (String::new(), repo)
}

/// This process's resident memory and CPU time, plus its reaped children's CPU (finished harnesses and git).
fn process_sample() -> Map<String, Value> {
    let mut f = Map::new();
    let usage = |who| {
        // SAFETY: getrusage fills the struct it is given.
        let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
        let ok = unsafe { libc::getrusage(who, &mut ru) } == 0;
        let ms = |t: libc::timeval| t.tv_sec as u64 * 1000 + t.tv_usec as u64 / 1000;
        ok.then(|| (ms(ru.ru_utime), ms(ru.ru_stime)))
    };
    if let Some((user, system)) = usage(libc::RUSAGE_SELF) {
        f.insert("cpu_user_ms".into(), json!(user));
        f.insert("cpu_system_ms".into(), json!(system));
    }
    if let Some((user, system)) = usage(libc::RUSAGE_CHILDREN) {
        f.insert("children_cpu_ms".into(), json!(user + system));
    }
    if let Some(rss) = resident_bytes() {
        f.insert("rss_bytes".into(), json!(rss));
    }
    f.insert("turns".into(), json!(TURNS.lock().unwrap_or_else(|p| p.into_inner()).as_ref().map_or(0, |t| t.len())));
    f.insert("dropped".into(), json!(DROPPED.swap(0, Ordering::Relaxed)));
    f
}

#[cfg(target_os = "macos")]
fn resident_bytes() -> Option<u64> {
    // SAFETY: proc_pidinfo writes at most `size` bytes into the struct.
    let mut info: libc::proc_taskinfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<libc::proc_taskinfo>() as libc::c_int;
    let got = unsafe { libc::proc_pidinfo(std::process::id() as libc::c_int, libc::PROC_PIDTASKINFO, 0, &mut info as *mut _ as *mut libc::c_void, size) };
    (got == size).then_some(info.pti_resident_size)
}

#[cfg(not(target_os = "macos"))]
fn resident_bytes() -> Option<u64> {
    let statm = std::fs::read_to_string("/proc/self/statm").ok()?;
    let pages: u64 = statm.split_whitespace().nth(1)?.parse().ok()?;
    // SAFETY: sysconf reads a constant.
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    Some(pages * page.max(4096) as u64)
}

/// Appends lines to `flight.ndjson`, rotating at `max_bytes`. A write error is retried on the next record, never
/// raised: the flight file must not stop the runner.
struct Writer {
    dir: PathBuf,
    max_bytes: u64,
    file: Option<File>,
    size: u64,
}

impl Writer {
    fn new(dir: &Path, max_bytes: u64) -> Self {
        Writer { dir: dir.to_path_buf(), max_bytes, file: None, size: 0 }
    }

    fn write(&mut self, record: &Map<String, Value>) {
        let mut line = Value::Object(record.clone()).to_string();
        line.push('\n');
        if self.file.is_some() && self.size + line.len() as u64 > self.max_bytes {
            self.rotate();
        }
        if self.file.is_none() {
            self.open();
            if self.file.is_some() && self.size > 0 && self.size + line.len() as u64 > self.max_bytes {
                // Left over from an earlier process: rotate and reopen once. If the rename failed and the file is
                // still too big, drop this record rather than grow it or try again.
                self.rotate();
                self.open();
                if self.size > 0 && self.size + line.len() as u64 > self.max_bytes {
                    self.file = None;
                    DROPPED.fetch_add(1, Ordering::Relaxed);
                    return;
                }
            }
        }
        if let Some(f) = self.file.as_mut() {
            if f.write_all(line.as_bytes()).is_ok() {
                self.size += line.len() as u64;
            } else {
                self.file = None;
            }
        }
    }

    fn open(&mut self) {
        use std::os::unix::fs::OpenOptionsExt;
        if let Ok(f) = OpenOptions::new().create(true).append(true).mode(0o600).open(self.dir.join(FILE)) {
            self.size = f.metadata().map(|m| m.len()).unwrap_or(0);
            self.file = Some(f);
        }
    }

    fn rotate(&mut self) {
        self.file = None;
        let name = |n: usize| if n == 0 { self.dir.join(FILE) } else { self.dir.join(format!("{FILE}.{n}")) };
        let _ = std::fs::remove_file(name(KEEP - 1));
        for n in (0..KEEP - 1).rev() {
            let _ = std::fs::rename(name(n), name(n + 1));
        }
        self.size = 0;
    }
}

/// The flight files in `dir`, oldest first.
fn files(dir: &Path) -> Vec<PathBuf> {
    (0..KEEP).rev().map(|n| if n == 0 { dir.join(FILE) } else { dir.join(format!("{FILE}.{n}")) }).filter(|p| p.exists()).collect()
}

/// `90s`, `30m`, `1h`, `2d` (a bare number is seconds) to seconds.
pub fn parse_since(text: &str) -> Option<f64> {
    let t = text.trim();
    let (num, unit) = t.split_at(t.find(|c: char| !c.is_ascii_digit() && c != '.').unwrap_or(t.len()));
    let n: f64 = num.parse().ok()?;
    let scale = match unit {
        "" | "s" => 1.0,
        "m" => 60.0,
        "h" => 3600.0,
        "d" => 86_400.0,
        _ => return None,
    };
    Some(n * scale)
}

/// `tico-runner flight summary`: what the flight files in `dir` say about the last `since_s` seconds. Reads only
/// the files, so it works with the runner stopped or on a copy of its state folder.
pub fn summary(dir: &Path, since_s: f64, now: f64) -> String {
    let cutoff = now - since_s;
    let mut turns: Vec<Value> = vec![];
    let mut heartbeats: Vec<Value> = vec![];
    let mut gits: Vec<Value> = vec![];
    let mut samples: Vec<Value> = vec![];
    let mut starts = 0;
    let found = files(dir);
    for path in &found {
        let Ok(f) = File::open(path) else { continue };
        for line in BufReader::new(f).lines().map_while(Result::ok) {
            let Ok(record) = serde_json::from_str::<Value>(&line) else { continue };
            if record_ts(&record) < cutoff {
                continue;
            }
            match record["kind"].as_str().unwrap_or("") {
                "runner.turn" => turns.push(record),
                "runner.heartbeat" => heartbeats.push(record),
                "runner.git" => gits.push(record),
                "runner.process" => samples.push(record),
                "runner.start" => starts += 1,
                _ => {}
            }
        }
    }
    let mut out = String::new();
    let say = |out: &mut String, line: String| {
        out.push_str(&line);
        out.push('\n');
    };
    if found.is_empty() {
        return format!("No flight file in {} (the Rust runner writes {FILE} there while it runs)\n", dir.display());
    }
    say(&mut out, format!("Flight file {} since {}", dir.join(FILE).display(), crate::util::text_of(&crate::util::iso(&json!(cutoff)))));
    if starts > 0 {
        say(&mut out, format!("Runner starts: {starts}"));
    }
    let secs = |ms: u64| format!("{:.1}s", ms as f64 / 1000.0);
    let n = |v: &Value| v.as_u64().unwrap_or(0);
    let clock = |v: &Value| crate::util::text_of(&crate::util::iso(&json!(record_ts(v)))).get(11..19).unwrap_or("").to_string();

    say(&mut out, format!("\nTurns: {}", turns.len()));
    // Each step is the gap from the previous mark the turn reached, in the order turns pass them.
    let mut steps: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for t in turns.iter().rev().take(20) {
        let mut line = format!("  {} {} {} {} {}", clock(t), t["bot"].as_str().unwrap_or(""), t["runtime"].as_str().unwrap_or(""), t["outcome"].as_str().unwrap_or(""), secs(n(&t["total_ms"])));
        for (name, ms) in reached(t) {
            if name != "claimed" {
                line.push_str(&format!(" {name}+{}", secs(ms)));
            }
        }
        say(&mut out, line);
    }
    for t in &turns {
        let marks = reached(t);
        for pair in marks.windows(2) {
            steps.entry(format!("{} -> {}", pair[0].0, pair[1].0)).or_default().push(pair[1].1.saturating_sub(pair[0].1));
        }
    }
    if !steps.is_empty() {
        say(&mut out, "\nSlowest phases (max, median, turns):".into());
        let mut rows: Vec<(String, u64, u64, usize)> = steps
            .into_iter()
            .map(|(k, mut v)| {
                v.sort_unstable();
                (k, *v.last().unwrap_or(&0), v[v.len() / 2], v.len())
            })
            .collect();
        rows.sort_by(|a, b| b.1.cmp(&a.1));
        for (step, max, median, count) in rows.iter().take(8) {
            say(&mut out, format!("  {step}: {} max, {} median, {count}", secs(*max), secs(*median)));
        }
    }

    let failed = heartbeats.iter().filter(|h| h["ok"] == false).count();
    say(&mut out, format!("\nSlow or failed heartbeats: {} ({failed} failed; slow is {}+)", heartbeats.len(), secs(HEARTBEAT_SLOW_MS)));
    heartbeats.sort_by_key(|h| std::cmp::Reverse(n(&h["ms"])));
    for h in heartbeats.iter().take(5) {
        let how = if h["ok"] == true { String::new() } else { format!(" failed ({} {})", h["status"], h["code"].as_str().unwrap_or("")) };
        say(&mut out, format!("  {} {} tries {}{how}", clock(h), secs(n(&h["ms"])), n(&h["tries"])));
    }

    say(&mut out, format!("\nSlow git commands: {} (slow is {}+)", gits.len(), secs(GIT_SLOW_MS)));
    gits.sort_by_key(|g| std::cmp::Reverse(n(&g["ms"])));
    for g in gits.iter().take(10) {
        let fault = g["fault"].as_str().map(|f| format!(" {f}")).unwrap_or_default();
        say(&mut out, format!("  {} git {} {} {}{fault}", clock(g), g["verb"].as_str().unwrap_or(""), g["repo"].as_str().unwrap_or(""), secs(n(&g["ms"]))));
    }

    let dropped: u64 = samples.iter().map(|s| n(&s["dropped"])).sum();
    if let Some(last) = samples.last() {
        let peak = samples.iter().map(|s| n(&s["rss_bytes"])).max().unwrap_or(0);
        say(&mut out, format!("\nProcess: {} MB resident now, {} MB peak; {} turns open", n(&last["rss_bytes"]) >> 20, peak >> 20, n(&last["turns"])));
    }
    say(&mut out, format!("Dropped records: {dropped}"));
    out
}

/// A turn record's marks in turn order, with their milliseconds since claimed.
fn reached(turn: &Value) -> Vec<(String, u64)> {
    let mut marks: Vec<(String, u64)> = PHASES.iter().filter_map(|p| turn["phases"][*p].as_u64().map(|ms| (p.to_string(), ms))).collect();
    marks.sort_by_key(|(name, ms)| (*ms, PHASES.iter().position(|p| p == name)));
    marks
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-flight-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_failed_rotation_drops_the_record() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp();
        std::fs::write(dir.join(FILE), "x".repeat(300)).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o500)).unwrap(); // renames fail
        let mut w = Writer::new(&dir, 200);
        w.write(&envelope("runner.git", Map::new()));
        assert_eq!(std::fs::metadata(dir.join(FILE)).unwrap().len(), 300);
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_file_rotates_and_keeps_five() {
        let dir = temp();
        let mut w = Writer::new(&dir, 200);
        for i in 0..40 {
            w.write(&envelope("runner.git", Map::from_iter([("ms".to_string(), json!(i))])));
        }
        let kept = files(&dir);
        assert_eq!(kept.len(), KEEP);
        assert!(kept.iter().all(|p| std::fs::metadata(p).unwrap().len() <= 200));
        let newest = std::fs::read_to_string(dir.join(FILE)).unwrap();
        assert!(newest.contains("\"ms\":39"), "{newest}");
        let first: Value = serde_json::from_str(newest.lines().next().unwrap()).unwrap();
        assert!(first["v"] == 1 && first["ts"].is_f64() && first["kind"] == "runner.git", "{first}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn summary_lists_turn_phases_and_slow_calls_in_the_window() {
        let dir = temp();
        let lines = [
            r#"{"v":1,"ts":1791968400.25,"kind":"runner.turn","bot":"stale-bot","runtime":"codex","outcome":"completed","total_ms":1,"phases":{"claimed":0}}"#,
            r#"{"v":1,"ts":1791977400.25,"kind":"runner.turn","attempt":"a1","bot":"acme-seo","runtime":"claude","outcome":"completed","total_ms":84000,"phases":{"claimed":0,"placed":1200,"harness_started":1500,"first_output":6000,"finished":80000,"complete_posted":81000,"done":84000}}"#,
            r#"{"v":1,"ts":1791977460.25,"kind":"runner.heartbeat","ms":4200,"tries":1,"ok":true}"#,
            r#"{"v":1,"ts":1791977520.25,"kind":"runner.git","verb":"fetch","repo":"acme-seo","ms":9000,"code":0}"#,
            r#"{"v":1,"ts":1791977580.25,"kind":"runner.process","rss_bytes":52428800,"turns":1,"dropped":3}"#,
            "not json",
            r#"{"v":1,"ts":1791977640.25,"kind":"server.request","ms":1}"#,
        ];
        std::fs::write(dir.join(FILE), lines.join("\n")).unwrap();
        let now = crate::util::parse_iso("2026-10-14T12:00:00Z");
        let text = summary(&dir, parse_since("1h").unwrap(), now);
        assert!(text.contains("Turns: 1"), "{text}");
        assert!(text.contains("acme-seo claude completed 84.0s placed+1.2s"), "{text}");
        assert!(text.contains("first_output -> finished: 74.0s max"), "{text}");
        assert!(text.contains("4.2s tries 1"), "{text}");
        assert!(text.contains("git fetch acme-seo 9.0s"), "{text}");
        assert!(text.contains("Dropped records: 3"), "{text}");
        assert!(!text.contains("stale-bot"), "{text}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
