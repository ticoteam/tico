//! What a harness process kept between turns shares (hosts/pi.rs Pool): how long one may sit idle, and the revocable
//! credential it reads. The process reads `HUB_TOKEN=tico-file:<home>/lease-token`, a file rewritten with each turn's
//! attempt token and blanked when the turn ends, so a process kept between turns holds no live token.

use std::path::Path;
use std::time::Duration;

pub const IDLE: Duration = Duration::from_secs(900);

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
