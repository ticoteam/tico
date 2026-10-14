//! `runner.json`: the registration Python's `runner enroll` wrote. Read as a JSON object and written back
//! with every key it holds, known or not, so a switch back to the Python runner loses nothing.

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

    /// The bot's explicit checkout from `repos`, when the registration names one.
    pub fn repo_for(&self, bot: &str) -> Option<PathBuf> {
        self.data.get("repos").and_then(|r| r.get(bot)).and_then(|p| p.as_str()).map(PathBuf::from)
    }
}

/// How many turns run at once: a number as written, else sized to the machine
/// (`runner/service.py` machine_capacity): a core and about 2 GB per turn, 4 to 16.
pub fn machine_capacity(configured: Option<&Value>) -> usize {
    match configured {
        Some(Value::Number(n)) => return n.as_i64().map(|v| v.max(1) as usize).unwrap_or(4),
        Some(Value::String(s)) if !s.is_empty() && s != "auto" => {
            if let Ok(v) = s.trim().parse::<i64>() {
                return v.max(1) as usize;
            }
        }
        _ => {}
    }
    let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4);
    let memory_gb = unsafe {
        let pages = libc::sysconf(libc::_SC_PHYS_PAGES);
        let size = libc::sysconf(libc::_SC_PAGESIZE);
        if pages > 0 && size > 0 { (pages as u64 * size as u64) >> 30 } else { 8 }
    } as usize;
    4.max(cpus.min(memory_gb / 2).min(16))
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn capacity_is_the_configured_number_or_four_to_sixteen() {
        assert_eq!(machine_capacity(Some(&Value::from(2))), 2);
        assert_eq!(machine_capacity(Some(&Value::from("3"))), 3);
        let auto = machine_capacity(Some(&Value::from("auto")));
        assert!((4..=16).contains(&auto));
    }
}
