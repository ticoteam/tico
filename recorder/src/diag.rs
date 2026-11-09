//! The recorder's local log: `recorder.log` in the library folder, also echoed to stderr.
//! It rolls over to `recorder.log.1` past 1 MB. Lines may carry names a meeting already shows,
//! never transcript text, keys or addresses.
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};

const MAX_BYTES: u64 = 1 << 20;
static PATH: OnceLock<PathBuf> = OnceLock::new();
static WRITE: Mutex<()> = Mutex::new(());

/// Called once by the app with its library folder; without it lines go to stderr only.
pub fn init(root: &Path) {
    let _ = PATH.set(root.join("recorder.log"));
}

pub fn line(args: std::fmt::Arguments) {
    let text = format!(
        "{} {args}\n",
        chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f")
    );
    eprint!("{text}");
    let Some(path) = PATH.get() else {
        return;
    };
    let _guard = WRITE.lock().unwrap_or_else(|e| e.into_inner());
    if fs::metadata(path).is_ok_and(|m| m.len() > MAX_BYTES) {
        let _ = fs::rename(path, path.with_extension("log.1"));
    }
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(text.as_bytes());
    }
}

/// `diag!("...")` writes one line to the recorder log.
#[macro_export]
macro_rules! diag {
    ($($arg:tt)*) => { $crate::diag::line(format_args!($($arg)*)) };
}

/// At most `per_minute` lines; the next allowed line reports how many were dropped.
pub struct Limit {
    per_minute: u32,
    since: Instant,
    count: u32,
    dropped: u32,
}
impl Limit {
    pub fn new(per_minute: u32) -> Self {
        Self {
            per_minute,
            since: Instant::now(),
            count: 0,
            dropped: 0,
        }
    }
    /// Some(dropped since the last allowed line) when this line may be written.
    pub fn allow(&mut self) -> Option<u32> {
        if self.since.elapsed() >= Duration::from_secs(60) {
            self.since = Instant::now();
            self.count = 0;
        }
        if self.count >= self.per_minute {
            self.dropped += 1;
            return None;
        }
        self.count += 1;
        Some(std::mem::take(&mut self.dropped))
    }
}
