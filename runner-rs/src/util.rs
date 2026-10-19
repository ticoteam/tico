//! Logging, time and the timing marks.

use std::sync::OnceLock;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;

static START: OnceLock<Instant> = OnceLock::new();

pub fn start_clock() {
    START.get_or_init(Instant::now);
}

/// Milliseconds on this process's monotonic clock.
pub fn mono_ms() -> u128 {
    START.get_or_init(Instant::now).elapsed().as_millis()
}

pub fn epoch_s() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn local_stamp() -> String {
    unsafe {
        let now = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        libc::localtime_r(&now, &mut tm);
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
            tm.tm_year + 1900,
            tm.tm_mon + 1,
            tm.tm_mday,
            tm.tm_hour,
            tm.tm_min,
            tm.tm_sec
        )
    }
}

/// Blocking work (git, file reads, the state database's fsyncs) run on tokio's blocking pool, awaited in place so
/// the caller's order holds. The runtime has two async workers; two turns' blocking calls on them would stall
/// every other turn's event flushes and result posts. A panic in `f` is the caller's panic, as before.
pub async fn off_worker<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    match tokio::task::spawn_blocking(f).await {
        Ok(v) => v,
        Err(e) if e.is_panic() => std::panic::resume_unwind(e.into_panic()),
        Err(e) => panic!("blocking work did not run: {e}"),
    }
}

/// One log line on stdout, as `runner/outage.py` log prints it, with every running turn's secrets taken out.
pub fn log(line: &str) {
    say(line, false);
}

/// A log line that always joins the heartbeat's `recent_errors` (outage.py `log(..., diagnostic=True)`), for a
/// line that reads as no trouble but is one Health and the support bundle should see.
pub fn diagnostic(line: &str) {
    say(line, true);
}

fn say(line: &str, diagnostic: bool) {
    let line = crate::redact::scrub_log(line);
    println!("{} {}", local_stamp(), line);
    static TROUBLE: OnceLock<regex::Regex> = OnceLock::new();
    let trouble = TROUBLE.get_or_init(|| {
        regex::Regex::new(r"(?i)error|fail|unavailable|refused|rejected|interrupted|cannot|could not|not updating|exception|limit").unwrap()
    });
    if diagnostic || trouble.is_match(&line) {
        remember(&line);
    }
}

/// The last lines that said something went wrong, for the heartbeat's `recent_errors` (outage.py RECENT).
static RECENT: std::sync::Mutex<std::collections::VecDeque<String>> = std::sync::Mutex::new(std::collections::VecDeque::new());

fn remember(line: &str) {
    let stamp = {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let iso = text_of(&iso(&serde_json::json!(now)));
        format!("{}Z", iso.trim_end_matches("+00:00"))
    };
    let mut recent = RECENT.lock().unwrap_or_else(|p| p.into_inner());
    if recent.len() == 50 {
        recent.pop_front();
    }
    recent.push_back(format!("{stamp} {}", line.replace('\n', " ")).chars().take(300).collect());
}

pub fn recent_errors() -> Vec<String> {
    RECENT.lock().unwrap_or_else(|p| p.into_inner()).iter().cloned().collect()
}

/// Whether `TICO_RUNNER_TIMINGS=1` asked for timing marks.
pub fn timings_on() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("TICO_RUNNER_TIMINGS").ok().as_deref() == Some("1"))
}

/// A structured timing line: `timing mark=<name> attempt=<id> mono_ms=<n> wall_ms=<n>`.
pub fn mark(name: &str, attempt: &str) {
    crate::flight::phase(name, attempt);
    if timings_on() {
        let wall = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
        log(&format!("Tico runner: timing mark={name} attempt={attempt} mono_ms={} wall_ms={wall}", mono_ms()));
    }
}

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian.
fn civil(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Epoch seconds as ISO-8601 UTC to the second, `+00:00` like Python's isoformat; None when not a number.
pub fn iso(epoch: &Value) -> Value {
    let secs = match epoch {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    };
    match secs {
        Some(s) if s.is_finite() => {
            let whole = s.floor() as i64;
            let (y, m, d) = civil(whole.div_euclid(86_400));
            let rem = whole.rem_euclid(86_400);
            Value::String(format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}+00:00", rem / 3600, rem % 3600 / 60, rem % 60))
        }
        _ => Value::Null,
    }
}

/// `2026-10-14T12:00:00Z` (or `+00:00`) to epoch seconds; 0 when it cannot be read.
pub fn parse_iso(text: &str) -> f64 {
    let t = text.trim();
    if t.len() < 19 {
        return 0.0;
    }
    let num = |a: usize, b: usize| t.get(a..b).and_then(|s| s.parse::<i64>().ok());
    let (Some(y), Some(mo), Some(d), Some(h), Some(mi), Some(s)) = (num(0, 4), num(5, 7), num(8, 10), num(11, 13), num(14, 16), num(17, 19)) else {
        return 0.0;
    };
    // days_from_civil
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = if mo > 2 { mo - 3 } else { mo + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let mut rest = &t[19..];
    let mut frac = 0.0;
    if let Some(stripped) = rest.strip_prefix('.') {
        let digits: String = stripped.chars().take_while(|c| c.is_ascii_digit()).collect();
        frac = format!("0.{digits}").parse::<f64>().unwrap_or(0.0);
        rest = &stripped[digits.len()..];
    }
    let mut offset = 0i64;
    if rest.starts_with('+') || rest.starts_with('-') {
        let sign = if rest.starts_with('-') { -1 } else { 1 };
        let hh = rest.get(1..3).and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        let mm = rest.get(4..6).and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        offset = sign * (hh * 3600 + mm * 60);
    }
    (days * 86_400 + h * 3600 + mi * 60 + s - offset) as f64 + frac
}

/// Python's `str(value)` for the plain JSON values the runner turns into text.
pub fn text_of(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Python truthiness of a JSON value.
pub fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// JSON as Python's `json.dumps(value, ensure_ascii=False)` writes it: `", "` and `": "` separators.
pub fn py_dumps(v: &Value) -> String {
    let mut out = String::new();
    write_py(v, &mut out);
    out
}

fn write_py(v: &Value, out: &mut String) {
    match v {
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (k, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&Value::String(k.clone()).to_string());
                out.push_str(": ");
                write_py(item, out);
            }
            out.push('}');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// Writes `body` to `path` as an executable script for a test to run. A child `cp` writes the file, so this
/// process never holds it open for writing: a thread forking at that moment would carry the descriptor into
/// its child, and running the script would fail with ETXTBSY ("Text file busy") until that child execs.
#[cfg(test)]
pub fn write_script(path: &std::path::Path, body: &str) {
    use std::os::unix::fs::PermissionsExt;
    let source = std::path::PathBuf::from(format!("{}.src", path.display()));
    std::fs::write(&source, body).unwrap();
    assert!(std::process::Command::new("cp").arg(&source).arg(path).status().unwrap().success());
    std::fs::remove_file(&source).unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn iso_round_trips_and_matches_pythons_isoformat() {
        assert_eq!(iso(&json!(1_760_000_000)), json!("2025-10-09T08:53:20+00:00"));
        assert_eq!(iso(&json!("x")), Value::Null);
        assert_eq!(parse_iso("2025-10-09T08:53:20Z"), 1_760_000_000.0);
        assert_eq!(parse_iso("2025-10-09T10:53:20+02:00"), 1_760_000_000.0);
    }

    #[test]
    fn py_dumps_spaces_like_python() {
        assert_eq!(py_dumps(&json!({"a": [1, "é"], "b": {"c": null}})), "{\"a\": [1, \"é\"], \"b\": {\"c\": null}}");
    }

    #[tokio::test]
    async fn a_panic_off_the_workers_is_the_callers_panic_and_nothing_after_it_runs() {
        // A scrub that panics must never read as an empty tree (nothing left out) and go on to publish.
        let reached = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let after = reached.clone();
        let ran = tokio::spawn(async move {
            let tree: crate::redact::Tree = off_worker(|| panic!("scrub failed")).await;
            after.store(true, std::sync::atomic::Ordering::SeqCst);
            tree
        })
        .await;
        assert!(ran.unwrap_err().is_panic());
        assert!(!reached.load(std::sync::atomic::Ordering::SeqCst));
    }
}
