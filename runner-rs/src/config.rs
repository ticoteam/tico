//! `runner.json`: the registration `tico-runner enroll` (or an older `python -m runner enroll`) wrote. Read as a
//! JSON object and written back with every key it holds, known or not, so nothing another version wrote is lost.

use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

#[derive(Clone, Debug)]
pub struct Config {
    pub path: PathBuf,
    pub data: Map<String, Value>,
}

impl Config {
    pub fn load(path: &Path) -> Result<Config, String> {
        let text = fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let value: Value = serde_json::from_str(&text).map_err(|e| format!("{} is not JSON: {e}", path.display()))?;
        match value {
            Value::Object(data) => Ok(Config { path: path.to_path_buf(), data }),
            _ => Err(format!("{} is not a JSON object", path.display())),
        }
    }

    /// Write it back atomically, 0600, keeping every key.
    #[allow(dead_code)]
    pub fn save(&self) -> Result<(), String> {
        let dir = self.path.parent().unwrap_or(Path::new("."));
        let tmp = dir.join(format!(".runner.json.{}", uuid::Uuid::new_v4().simple()));
        let text = serde_json::to_string_pretty(&Value::Object(self.data.clone())).unwrap();
        let result = (|| -> std::io::Result<()> {
            let mut file = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp)?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
            fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600))?;
            fs::rename(&tmp, &self.path)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&tmp);
        }
        result.map_err(|e| format!("cannot write {}: {e}", self.path.display()))
    }

    pub fn str(&self, key: &str) -> String {
        match self.data.get(key) {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Null) | None => String::new(),
            Some(other) => other.to_string(),
        }
    }

    pub fn url(&self) -> String {
        self.str("url")
    }
    pub fn token(&self) -> String {
        self.str("token")
    }
    pub fn runner_id(&self) -> String {
        self.str("runner_id")
    }
    pub fn projects_dir(&self) -> PathBuf {
        PathBuf::from(self.str("projects_dir"))
    }

    /// `<config dir>/state-<runner_id>`, the directory both engines share.
    pub fn state_dir(&self) -> PathBuf {
        self.path.parent().unwrap_or(Path::new(".")).join(format!("state-{}", self.runner_id()))
    }

    /// This registration with `fresh`'s subscription profile keys in place of its own (a key `fresh` lacks is
    /// dropped); every other key stays as it was read at start.
    pub fn with_profiles_of(&self, fresh: &Map<String, Value>) -> Config {
        let mut config = self.clone();
        for key in PROFILE_KEYS {
            match fresh.get(*key) {
                Some(value) => config.data.insert((*key).into(), value.clone()),
                None => config.data.shift_remove(*key),
            };
        }
        config
    }

    /// The bot's explicit checkout from `repos`, when the registration names one.
    pub fn repo_for(&self, bot: &str) -> Option<PathBuf> {
        self.data.get("repos").and_then(|r| r.get(bot)).and_then(|p| p.as_str()).map(PathBuf::from)
    }
}

/// The keys that name subscription profiles. A browser sign-in, `profile add` or a hand edit changes them while the
/// runner runs, so the heartbeat and turns read them from the file again (Runner::config_now).
pub const PROFILE_KEYS: &[&str] = &["profiles", "default_profile", "bot_profiles"];

/// A turn mostly waits on its model provider, not on this computer's CPU.
const TURNS_PER_CPU: usize = 5;
/// A model CLI and its tools take about 300 MB.
const TURNS_PER_GB: f64 = 3.0;
const MAX_AUTO_TURNS: usize = 64;
/// The most the server accepts.
pub const MAX_TURNS: usize = 256;

/// Setups before Oct 14, 2026 wrote `"capacity": 4` into runner.json, which kept those computers at 4 turns after
/// sizing moved to 5 per core. That one value becomes "auto" once (`capacity_auto` marks it done), so a person who
/// sets 4 again afterwards keeps it. Returns whether the file changed.
pub fn retire_old_capacity(data: &mut Map<String, Value>) -> bool {
    if data.contains_key("capacity_auto") {
        return false;
    }
    if data.get("capacity").and_then(Value::as_i64) == Some(4) {
        data.insert("capacity".into(), Value::from("auto"));
    }
    data.insert("capacity_auto".into(), Value::from(true));
    true
}

/// How many turns run at once (machine_capacity): a number as written, up to MAX_TURNS; missing
/// or "auto" is sized to the machine, TURNS_PER_CPU per core as memory allows (TURNS_PER_GB), 4 to MAX_AUTO_TURNS.
pub fn machine_capacity(configured: Option<&Value>) -> usize {
    let set = match configured {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Some(Value::String(s)) if !s.is_empty() && s != "auto" => s.trim().parse::<i64>().ok(),
        _ => None,
    };
    if let Some(v) = set {
        return v.clamp(1, MAX_TURNS as i64) as usize;
    }
    let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let memory_gb = unsafe {
        let pages = libc::sysconf(libc::_SC_PHYS_PAGES);
        let size = libc::sysconf(libc::_SC_PAGESIZE);
        if pages > 0 && size > 0 { pages as f64 * size as f64 / (1u64 << 30) as f64 } else { 8.0 }
    };
    sized(cpus, memory_gb)
}

fn sized(cpus: usize, memory_gb: f64) -> usize {
    4.max((cpus * TURNS_PER_CPU).min((memory_gb * TURNS_PER_GB) as usize).min(MAX_AUTO_TURNS))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_old_setups_four_turns_become_auto_once_and_a_chosen_number_stays() {
        let mut old: Map<String, Value> = serde_json::from_str(r#"{"capacity":4}"#).unwrap();
        assert!(retire_old_capacity(&mut old));
        assert_eq!(old["capacity"], "auto");
        old.insert("capacity".into(), Value::from(4));
        assert!(!retire_old_capacity(&mut old));
        assert_eq!(old["capacity"], 4);
        let mut chosen: Map<String, Value> = serde_json::from_str(r#"{"capacity":12}"#).unwrap();
        assert!(retire_old_capacity(&mut chosen));
        assert_eq!(chosen["capacity"], 12);
    }

    #[test]
    fn unknown_keys_survive_a_round_trip_and_the_file_stays_private() {
        let dir = std::env::temp_dir().join(format!("tico-config-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("runner.json");
        fs::write(&path, r#"{"runner_id":"r1","token":"t","url":"http://127.0.0.1:1","projects_dir":"/p","mail_key":{"x":1},"zeta":[1,2]}"#).unwrap();
        let mut config = Config::load(&path).unwrap();
        config.data.insert("capacity".into(), Value::from(2));
        config.save().unwrap();
        let again = Config::load(&path).unwrap();
        assert_eq!(again.data["mail_key"], serde_json::json!({"x": 1}));
        assert_eq!(again.data["zeta"], serde_json::json!([1, 2]));
        assert_eq!(again.data.keys().next().unwrap(), "runner_id");
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(again.state_dir(), dir.join("state-r1"));
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn only_the_profile_keys_are_taken_from_a_fresh_read() {
        let start: Map<String, Value> = serde_json::from_str(r#"{"url":"http://a","profiles":{"old":{"dir":"/p/old"}},"default_profile":"old"}"#).unwrap();
        let config = Config { path: PathBuf::from("/x/runner.json"), data: start };
        let disk: Map<String, Value> = serde_json::from_str(r#"{"url":"http://b","profiles":{"new":{"dir":"/p/new"}},"bot_profiles":{"ops":"new"}}"#).unwrap();
        let now = config.with_profiles_of(&disk);
        assert_eq!(now.data["profiles"], serde_json::json!({"new": {"dir": "/p/new"}}));
        assert_eq!(now.data["bot_profiles"], serde_json::json!({"ops": "new"}));
        assert!(!now.data.contains_key("default_profile"), "a key taken out of the file goes");
        assert_eq!(now.url(), "http://a", "everything else stays as read at start");
        assert_eq!(config.data["profiles"], serde_json::json!({"old": {"dir": "/p/old"}}));
    }

    #[test]
    fn capacity_is_five_turns_per_core_as_memory_allows_and_a_set_one_is_kept_to_256() {
        assert_eq!(machine_capacity(Some(&Value::from(2))), 2);
        assert_eq!(machine_capacity(Some(&Value::from("3"))), 3);
        assert_eq!(machine_capacity(Some(&Value::from(80))), 80);
        assert_eq!(machine_capacity(Some(&Value::from(1000))), 256);
        assert_eq!((sized(2, 16.0), sized(2, 2.0), sized(1, 1.0), sized(64, 512.0)), (10, 6, 4, 64));
        assert!(machine_capacity(Some(&Value::from("auto"))) >= 4);
    }
}
