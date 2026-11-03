//! A Tico cloud machine (runner/cloud_machine.py `base`, docs/cloud-machines.md): one company's VM, where every bot runs
//! as the machine's one user, who may use sudo on purpose. Its bots are that company's trust group, so a turn on a Tico
//! model runs here without a leased user of its own (gateway_relay.rs `Separation::TrustGroup`). The Gateway key still
//! stays in this runner's relay, never in a turn's environment or files; that the company's own bots could read it from
//! this process is the trade-off the company accepts (SECURITY.md).

use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::sync::OnceLock;

/// Written as root by `base` when the image is prepared, so every machine made from the snapshot has it, enrolled
/// before this check existed or not.
pub const MARKER: &str = "/etc/tico/cloud-machine.json";
/// The user `base` prepares when the marker names none (cloud_machine.py DEFAULT_USER).
const DEFAULT_USER: &str = "user";

/// Whether this runner runs on a cloud machine, as the user its marker names. Read once.
pub fn detected() -> bool {
    static FOUND: OnceLock<bool> = OnceLock::new();
    // SAFETY: geteuid has no preconditions.
    *FOUND.get_or_init(|| cfg!(target_os = "linux") && marked(Path::new(MARKER), 0, &user_name(unsafe { libc::geteuid() })))
}

/// The marker at `path` is a regular file in a directory, both owned by `owner` and changeable by no one else, and it
/// names `user`. Anything else (no file, a link, a file a user could have written, another user's) is not one.
fn marked(path: &Path, owner: u32, user: &str) -> bool {
    let sound = |p: &Path, file: bool| {
        std::fs::symlink_metadata(p).is_ok_and(|m| (if file { m.is_file() } else { m.is_dir() }) && m.uid() == owner && m.mode() & 0o022 == 0)
    };
    if user.is_empty() || !sound(path, true) || !path.parent().is_some_and(|d| sound(d, false)) {
        return false;
    }
    let Ok(doc) = std::fs::read_to_string(path).map(|t| serde_json::from_str::<serde_json::Value>(&t).unwrap_or_default()) else {
        return false;
    };
    doc.is_object() && doc["user"].as_str().filter(|u| !u.is_empty()).unwrap_or(DEFAULT_USER) == user
}

/// The login name of `uid`, or "" when it has none.
fn user_name(uid: u32) -> String {
    let mut buf = vec![0u8; 4096];
    let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
    let mut found: *mut libc::passwd = std::ptr::null_mut();
    // SAFETY: the buffer and entry outlive the call, and the name is read only when the call found an entry.
    let rc = unsafe { libc::getpwuid_r(uid, &mut entry, buf.as_mut_ptr().cast(), buf.len(), &mut found) };
    if rc != 0 || found.is_null() || entry.pw_name.is_null() {
        return String::new();
    }
    // SAFETY: pw_name points into `buf`, NUL-terminated by getpwuid_r.
    unsafe { std::ffi::CStr::from_ptr(entry.pw_name) }.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn me() -> u32 {
        // SAFETY: geteuid has no preconditions.
        unsafe { libc::geteuid() }
    }

    fn marker(name: &str, text: &str, mode: u32) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-cloud-marker-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o755)).unwrap();
        let path = dir.join("cloud-machine.json");
        std::fs::write(&path, text).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
        path
    }

    #[test]
    fn the_marker_base_writes_names_this_runners_user() {
        let path = marker("ok", r#"{"user": "user", "root": "/home/user/tico", "release": "v0.5.13"}"#, 0o644);
        assert!(marked(&path, me(), "user"));
        // Another user's runner, an owner other than the expected one, or no user at all: not this machine's runner.
        assert!(!marked(&path, me(), "someone"));
        assert!(!marked(&path, me().wrapping_add(1), "user"));
        assert!(!marked(&path, me(), ""));
        // A marker naming no user means base's default user.
        let unnamed = marker("unnamed", r#"{"root": "/home/user/tico"}"#, 0o644);
        assert!(marked(&unnamed, me(), "user") && !marked(&unnamed, me(), "root"));
    }

    #[test]
    fn a_marker_others_could_write_or_that_is_not_one_counts_for_nothing() {
        assert!(!marked(&marker("writable", r#"{"user": "user"}"#, 0o666), me(), "user"));
        assert!(!marked(&marker("garbled", "not json", 0o644), me(), "user"));
        assert!(!marked(&marker("list", r#"["user"]"#, 0o644), me(), "user"));
        assert!(!marked(Path::new("/nonexistent/tico/cloud-machine.json"), me(), "user"));
        let open_dir = marker("open-dir", r#"{"user": "user"}"#, 0o644);
        std::fs::set_permissions(open_dir.parent().unwrap(), std::fs::Permissions::from_mode(0o777)).unwrap();
        assert!(!marked(&open_dir, me(), "user"));
        let real = marker("real", r#"{"user": "user"}"#, 0o644);
        let link = real.with_file_name("link.json");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert!(!marked(&link, me(), "user"));
    }

    #[test]
    fn this_processs_user_has_a_name() {
        assert!(!user_name(me()).is_empty());
    }
}
