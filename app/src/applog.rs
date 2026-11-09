//! The shell's own log file. An app opened from Finder or the Dock has no terminal, so stderr goes nowhere; every
//! line also goes to `tico.log` in the app's log folder (~/Library/Logs/<bundle id>/ on macOS), which Help opens and
//! Support diagnostics attach the end of. At MAX_BYTES the file moves to `tico.log.1`, so two files at most are kept.
//! This crate logs at info and above, other crates at warn and above; `RUST_LOG=<level>` sets one level for all.
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

pub const FILE_NAME: &str = "tico.log";
const MAX_BYTES: u64 = 1024 * 1024;
// What `tail` reads from the end of each file: enough for its lines, never the whole file.
const TAIL_BYTES: u64 = 256 * 1024;
// Lines logged before the log folder is known (plugin start-up), written to the file once it is.
const EARLY_LINES: usize = 200;

static LOGGER: OnceLock<FileLog> = OnceLock::new();

struct FileLog {
    level: Option<log::LevelFilter>,
    max: u64,
    // Written as `~`, so the log (and diagnostics made from it) does not name the person's account.
    home: Option<String>,
    sink: Mutex<Sink>,
}

#[derive(Default)]
struct Sink {
    path: Option<PathBuf>,
    file: Option<File>,
    early: Vec<String>,
}

fn open(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn rotated(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(".1");
    PathBuf::from(name)
}

fn home() -> Option<String> {
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    let home = home
        .to_string_lossy()
        .trim_end_matches(['/', '\\'])
        .to_string();
    (home.len() > 1).then_some(home)
}

impl FileLog {
    fn new(level: Option<log::LevelFilter>, max: u64, home: Option<String>) -> Self {
        FileLog {
            level,
            max,
            home,
            sink: Mutex::new(Sink::default()),
        }
    }

    fn sink(&self) -> std::sync::MutexGuard<'_, Sink> {
        self.sink.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Starts writing to `dir`'s log file, beginning with what was logged before. None when it cannot be written.
    fn attach(&self, dir: &Path) -> Option<PathBuf> {
        std::fs::create_dir_all(dir).ok()?;
        let path = dir.join(FILE_NAME);
        let file = open(&path).ok()?;
        let mut sink = self.sink();
        let early = std::mem::take(&mut sink.early);
        sink.path = Some(path.clone());
        sink.file = Some(file);
        for line in early {
            self.write_line(&mut sink, &line);
        }
        Some(path)
    }

    fn write_line(&self, sink: &mut Sink, line: &str) {
        let Some(path) = sink.path.clone() else {
            if sink.early.len() < EARLY_LINES {
                sink.early.push(line.to_string());
            }
            return;
        };
        let full = sink
            .file
            .as_ref()
            .and_then(|f| f.metadata().ok())
            .map(|m| m.len() + line.len() as u64 > self.max);
        if full.unwrap_or(false) {
            // Closed before the rename, which Windows refuses on an open file.
            sink.file = None;
            let _ = std::fs::rename(&path, rotated(&path));
            sink.file = open(&path).ok();
        }
        if let Some(f) = sink.file.as_mut() {
            let _ = f.write_all(line.as_bytes());
        }
    }

    fn line(&self, record: &log::Record) -> String {
        let line = format!(
            "{} {} {}: {}\n",
            timestamp(SystemTime::now()),
            record.level(),
            record.target(),
            record.args()
        );
        match &self.home {
            Some(home) => line.replace(home.as_str(), "~"),
            None => line,
        }
    }
}

impl log::Log for FileLog {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        let level =
            self.level
                .unwrap_or(if metadata.target().starts_with(env!("CARGO_CRATE_NAME")) {
                    log::LevelFilter::Info
                } else {
                    log::LevelFilter::Warn
                });
        metadata.level() <= level
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let line = self.line(record);
        eprint!("{line}");
        self.write_line(&mut self.sink(), &line);
    }

    fn flush(&self) {
        if let Some(f) = self.sink().file.as_mut() {
            let _ = f.flush();
        }
    }
}

/// Logs to stderr from the first line; `attach` adds the file once the app knows its log folder.
pub fn init() {
    let level = std::env::var("RUST_LOG").ok().and_then(|v| v.parse().ok());
    let logger = LOGGER.get_or_init(|| FileLog::new(level, MAX_BYTES, home()));
    log::set_max_level(level.unwrap_or(log::LevelFilter::Info));
    let _ = log::set_logger(logger);
}

/// Writes the log to `dir` from now on, starting with the lines logged before. Returns the file's path, or None
/// when it cannot be written (stderr alone).
pub fn attach(dir: Option<&Path>) -> Option<PathBuf> {
    LOGGER.get()?.attach(dir?)
}

/// `path` with the home folder as `~`, as every logged line has it.
pub fn shown(path: &Path) -> String {
    let text = path.display().to_string();
    match home() {
        Some(home) => text.replace(home.as_str(), "~"),
        None => text,
    }
}

/// The last `keep` lines of the log, the rotated file's end first when the current one is short.
pub fn tail(path: &Path, keep: usize) -> Vec<String> {
    let mut lines = Vec::new();
    for file in [rotated(path), path.to_path_buf()] {
        lines.extend(tail_of(&file));
    }
    let skip = lines.len().saturating_sub(keep);
    lines.split_off(skip)
}

fn tail_of(path: &Path) -> Vec<String> {
    let Ok(mut file) = File::open(path) else {
        return Vec::new();
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let start = len.saturating_sub(TAIL_BYTES);
    let mut bytes = Vec::new();
    if file.seek(SeekFrom::Start(start)).is_err() || file.read_to_end(&mut bytes).is_err() {
        return Vec::new();
    }
    let text = String::from_utf8_lossy(&bytes);
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    // A read that starts mid-file starts mid-line.
    if start > 0 && !lines.is_empty() {
        lines.remove(0);
    }
    lines
}

/// `2026-10-16T06:47:31Z`, UTC, without a date library.
fn timestamp(now: SystemTime) -> String {
    let secs = now
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let (days, rest) = (secs / 86400, secs % 86400);
    // Civil date from days since 1970-01-01 (Howard Hinnant's days_from_civil, inverted).
    let z = days as i64 + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + if month <= 2 { 1 } else { 0 };
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use log::Log;
    use std::time::Duration;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-applog-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    fn line(logger: &FileLog, text: &str) {
        logger.log(
            &log::Record::builder()
                .level(log::Level::Info)
                .target(env!("CARGO_CRATE_NAME"))
                .args(format_args!("{text}"))
                .build(),
        );
    }

    #[test]
    fn the_log_rotates_to_one_old_file_and_tail_reads_across_both() {
        let dir = scratch("rotate");
        let logger = FileLog::new(None, 200, None);
        assert!(logger.attach(&dir).is_some());
        for n in 0..12 {
            line(&logger, &format!("update check {n}: current"));
        }
        let path = dir.join(FILE_NAME);
        assert!(std::fs::metadata(&path).unwrap().len() <= 200);
        assert!(rotated(&path).exists());
        assert!(!dir.join("tico.log.1.1").exists());
        let lines = tail(&path, 3);
        assert_eq!(lines.len(), 3);
        assert!(lines[2].ends_with("update check 11: current"), "{lines:?}");
        assert!(lines[0].ends_with("update check 9: current"), "{lines:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn lines_before_the_folder_is_known_reach_the_file_and_the_home_folder_is_never_written() {
        let dir = scratch("early");
        let logger = FileLog::new(None, MAX_BYTES, Some("/Users/ana".into()));
        line(&logger, "plugin started");
        line(
            &logger,
            "could not read /Users/ana/Library/Application Support/x",
        );
        logger.attach(&dir).unwrap();
        line(&logger, "after");
        let lines = tail(&dir.join(FILE_NAME), 10);
        assert_eq!(lines.len(), 3, "{lines:?}");
        assert!(
            lines[0].ends_with("plugin started") && lines[2].ends_with("after"),
            "{lines:?}"
        );
        assert!(
            lines[1].ends_with("could not read ~/Library/Application Support/x"),
            "{lines:?}"
        );
        assert!(!std::fs::read_to_string(dir.join(FILE_NAME))
            .unwrap()
            .contains("ana"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn other_crates_log_warnings_only_and_without_a_folder_nothing_panics() {
        let logger = FileLog::new(None, MAX_BYTES, None);
        let info = |target| {
            log::Metadata::builder()
                .level(log::Level::Info)
                .target(target)
                .build()
        };
        assert!(logger.enabled(&info(env!("CARGO_CRATE_NAME"))));
        assert!(!logger.enabled(&info("hyper::client")));
        for _ in 0..EARLY_LINES + 5 {
            line(&logger, "no file, no panic");
        }
        assert_eq!(logger.sink().early.len(), EARLY_LINES);
    }

    #[test]
    fn timestamps_are_utc_dates() {
        assert_eq!(timestamp(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        assert_eq!(
            timestamp(UNIX_EPOCH + Duration::from_secs(1_792_133_251)),
            "2026-10-16T06:47:31Z"
        );
        assert_eq!(
            timestamp(UNIX_EPOCH + Duration::from_secs(951_782_400)),
            "2000-02-29T00:00:00Z"
        );
    }
}
