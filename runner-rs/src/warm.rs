//! What a harness process kept between turns shares (hosts/pi.rs Pool and crate::keeper): how long one may sit
//! idle, and the revocable credential it reads. The process reads `HUB_TOKEN=tico-file:<home>/lease-token`, a file rewritten with each turn's
//! attempt token and blanked when the turn ends, so a process kept between turns holds no live token.

use std::path::{Path, PathBuf};
use std::time::Duration;

/// How long pi's kept process may sit idle (hosts/pi.rs Pool). Claude, Codex and Antigravity are kept by
/// crate::keeper, for T3's two minutes.
pub const IDLE: Duration = Duration::from_secs(900);

/// What each attempt's environment gives a kept process that is not compared for its reuse: the hub token (read from
/// the token file) and the attempt's scope stem (crate::turn_limits, set for every attempt where turns are limited: the
/// process stays in the scope it was started in, `turn_limits::launch_unit`).
pub const PER_ATTEMPT: [&str; 2] = ["HUB_TOKEN", crate::turn_limits::SCOPE_ENV];

/// A new folder under `dir` for one kept process's token file: the bot may traverse it, never write it.
pub fn folder(dir: &Path) -> Result<PathBuf, String> {
    use std::os::unix::fs::PermissionsExt;
    let home = dir.join(uuid::Uuid::new_v4().simple().to_string());
    std::fs::create_dir_all(&home).map_err(|e| format!("could not make a token folder: {e}"))?;
    let mode = if crate::isolation::enabled() {
        0o711
    } else {
        0o700
    };
    for path in [dir, home.as_path()] {
        if !std::fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .is_dir()
        {
            return Err("token folders must not be symbolic links".into());
        }
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
            .map_err(|e| format!("could not prepare a token folder: {e}"))?;
    }
    Ok(home)
}

/// Write the token file, owned by `owner` (a leased turn user) in place of the bot user.
pub fn write_token_for(path: &Path, token: &str, owner: Option<(u32, u32)>) {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4().simple()));
    let written = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .and_then(|mut f| {
            f.write_all(token.as_bytes())?;
            match owner {
                // SAFETY: fchown on a descriptor this process owns.
                Some((uid, gid))
                    if unsafe { libc::fchown(std::os::fd::AsRawFd::as_raw_fd(&f), uid, gid) }
                        != 0 =>
                {
                    Err(std::io::Error::last_os_error())
                }
                Some(_) => Ok(()),
                None => crate::isolation::chown_file(&f),
            }
        })
        .and_then(|_| std::fs::rename(&tmp, path));
    let _ = std::fs::remove_file(&tmp);
    if let Err(e) = written {
        crate::util::log(&format!(
            "Tico runner: could not write a kept process's token file ({e})"
        ));
    }
}
