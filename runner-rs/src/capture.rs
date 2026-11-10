//! A turn's raw provider stream, both ways, for a bot whose `capture_stream` is on: every frame the runner
//! wrote to the harness and every frame it read back, redacted with the running turns' secrets, capped at
//! LIMIT bytes, and handed to the server when the turn ends (`tico run export --fixture`).
//!
//! One record per line: `{"dir":"start","provider":..}` when a harness attaches (a fallback adds a second),
//! then `{"dir":"out"|"in","ms":..,"frame":..}` with the frame as JSON when it parses and as text when not,
//! and `{"dir":"skipped","bytes":..}` for a frame left out because it no longer fits under the cap.

use std::io::{Read, Seek, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};

use serde_json::{json, Value};

pub const LIMIT: u64 = 5_000_000;
/// Room past LIMIT for skip markers, so a long run over the cap still says what it left out.
const MARKS: u64 = 100_000;
/// A file a runner never handed in (it stopped mid-turn) is gone after the same week the server keeps one.
const KEEP: Duration = Duration::from_secs(7 * 24 * 3600);

pub fn wanted(config: &Value) -> bool {
    config.get("capture_stream").and_then(|v| v.as_str()) == Some("on")
}

pub struct Capture {
    path: PathBuf,
    file: Mutex<Option<std::fs::File>>,
    started: Instant,
    written: AtomicU64,
    dropped: AtomicU64,
    truncated: AtomicBool,
    provider: Mutex<String>,
}

impl Capture {
    pub fn open(state_dir: &Path, aid: &str) -> std::io::Result<Arc<Capture>> {
        let dir = state_dir.join("captures");
        std::fs::create_dir_all(&dir)?;
        sweep(&dir);
        let path = dir.join(format!("{aid}.ndjson"));
        use std::os::unix::fs::OpenOptionsExt;
        // Customer text, even scrubbed: readable by the runner alone.
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)?;
        Ok(Arc::new(Capture {
            path,
            file: Mutex::new(Some(file)),
            started: Instant::now(),
            written: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            truncated: AtomicBool::new(false),
            provider: Mutex::new(String::new()),
        }))
    }

    /// A harness attaches; a fallback's harness writes its own start record into the same stream.
    pub fn attach(&self, provider: &str) {
        let mut first = self.provider.lock().unwrap();
        if first.is_empty() {
            *first = provider.to_string();
        }
        drop(first);
        self.record(json!({"dir": "start", "provider": provider, "ms": self.ms()}));
    }

    pub fn sent(&self, line: &str) {
        self.frame("out", line);
    }

    pub fn received(&self, line: &str) {
        self.frame("in", line);
    }

    fn ms(&self) -> u64 {
        self.started.elapsed().as_millis() as u64
    }

    fn frame(&self, dir: &str, line: &str) {
        let line = line.trim();
        if line.is_empty() {
            return;
        }
        // Scrubbed as written and again inside each string once parsed, where JSON escaping can hide a value.
        let raw = crate::redact::scrub_log(line);
        let frame = serde_json::from_str::<Value>(&raw)
            .map(|v| scrub_strings(&v))
            .unwrap_or(Value::String(raw));
        self.record(json!({"dir": dir, "ms": self.ms(), "frame": frame}));
    }

    fn record(&self, rec: Value) {
        let text = crate::redact::scrub_log(&rec.to_string()) + "\n";
        let mut slot = self.file.lock().unwrap();
        let Some(file) = slot.as_mut() else { return };
        let size = text.len() as u64;
        let at = self.written.load(Ordering::Relaxed);
        if at + size > LIMIT {
            // A frame that does not fit is skipped with a marker and the stream goes on: a run's ending is
            // usually what a replay needs. Markers have a small reserve past LIMIT of their own.
            self.dropped.fetch_add(size, Ordering::Relaxed);
            self.truncated.store(true, Ordering::Relaxed);
            let mark = json!({"dir": "skipped", "ms": self.ms(), "bytes": size}).to_string() + "\n";
            if at + (mark.len() as u64) <= LIMIT + MARKS && file.write_all(mark.as_bytes()).is_ok()
            {
                self.written.fetch_add(mark.len() as u64, Ordering::Relaxed);
            }
            return;
        }
        if file.write_all(text.as_bytes()).is_ok() {
            self.written.fetch_add(size, Ordering::Relaxed);
        }
    }

    /// The stream for the server, the file removed: `{provider, frames, truncated, dropped_bytes}`, or None when
    /// nothing was captured.
    pub fn finish(&self) -> Option<Value> {
        let mut file = self.file.lock().unwrap().take()?;
        let _ = std::fs::remove_file(&self.path);
        let mut frames = String::new();
        file.rewind().ok()?;
        file.read_to_string(&mut frames).ok()?;
        let provider = self.provider.lock().unwrap().clone();
        if frames.is_empty() || provider.is_empty() {
            return None;
        }
        Some(json!({
            "provider": provider,
            "frames": frames,
            "truncated": self.truncated.load(Ordering::Relaxed),
            "dropped_bytes": self.dropped.load(Ordering::Relaxed),
        }))
    }
}

impl Drop for Capture {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn scrub_strings(v: &Value) -> Value {
    match v {
        Value::String(s) => Value::String(crate::redact::scrub_log(s)),
        Value::Array(items) => Value::Array(items.iter().map(scrub_strings).collect()),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, i)| (k.clone(), scrub_strings(i)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn sweep(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let now = SystemTime::now();
    for e in entries.flatten() {
        let old = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| now.duration_since(t).ok())
            .is_some_and(|age| age > KEEP);
        if old {
            let _ = std::fs::remove_file(e.path());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::redact::{register, release, Redactor};

    fn dir() -> PathBuf {
        let d =
            std::env::temp_dir().join(format!("tico-capture-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn frames_are_kept_both_ways_with_secrets_masked_in_every_form() {
        let d = dir();
        let aid = format!("cap-{}", uuid::Uuid::new_v4().simple());
        register(
            &aid,
            Arc::new(Redactor::new(&[
                "granted-secret-0123".into(),
                "quo\"ted-secret-456".into(),
            ])),
        );
        let c = Capture::open(&d, &aid).unwrap();
        c.attach("claude");
        c.sent(r#"{"type":"user","message":{"content":"use granted-secret-0123"}}"#);
        c.received(r#"{"type":"assistant","text":"quo\"ted-secret-456 and more"}"#);
        c.received("not json granted-secret-0123");
        c.received("   ");
        let out = c.finish().unwrap();
        release(&aid);
        let frames = out["frames"].as_str().unwrap();
        assert!(
            !frames.contains("granted-secret-0123") && !frames.contains("ted-secret-456"),
            "{frames}"
        );
        let rows: Vec<Value> = frames
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(
            rows.iter()
                .map(|r| r["dir"].as_str().unwrap())
                .collect::<Vec<_>>(),
            ["start", "out", "in", "in"]
        );
        assert_eq!(rows[1]["frame"]["type"], "user", "a JSON frame stays JSON");
        assert_eq!(rows[3]["frame"], "not json ••••");
        assert_eq!(
            (out["provider"].as_str(), out["truncated"].as_bool()),
            (Some("claude"), Some(false))
        );
        assert!(
            !d.join("captures").join(format!("{aid}.ndjson")).exists(),
            "the local file is gone once handed in"
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_frame_past_the_cap_is_skipped_and_later_ones_still_fit() {
        let d = dir();
        let c = Capture::open(&d, "cap-limit").unwrap();
        c.attach("codex");
        let big = format!(
            r#"{{"method":"item/delta","params":{{"text":"{}"}}}}"#,
            "x".repeat(400_000)
        );
        for _ in 0..20 {
            c.received(&big);
        }
        c.received(r#"{"method":"turn/completed"}"#);
        let out = c.finish().unwrap();
        let frames = out["frames"].as_str().unwrap();
        assert!(frames.len() as u64 <= LIMIT + MARKS, "{}", frames.len());
        assert!(
            frames
                .lines()
                .filter(|l| l.contains(r#""dir":"skipped""#))
                .count()
                >= 8
        );
        assert!(
            frames
                .trim_end()
                .ends_with(r#"{"method":"turn/completed"}}"#),
            "the ending is kept"
        );
        assert!(out["truncated"].as_bool().unwrap() && out["dropped_bytes"].as_u64().unwrap() > 0);
        let _ = std::fs::remove_dir_all(&d);
    }
}
