//! Bounded supervisor access to files in bot-writable checkouts and Git metadata.

use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

pub const SMALL: u64 = 4096;
pub const CONTENT: u64 = 1024 * 1024;

fn invalid() -> io::Error { io::Error::new(io::ErrorKind::InvalidData, "not a bounded regular file") }

/// Check the opened inode, since a pathname's earlier metadata can already be stale.
pub fn open(path: &Path, options: &mut std::fs::OpenOptions, limit: u64) -> io::Result<std::fs::File> {
    let file = options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > limit { return Err(invalid()); }
    Ok(file)
}

pub fn read(path: impl AsRef<Path>, limit: u64) -> io::Result<Vec<u8>> {
    let file = open(path.as_ref(), std::fs::OpenOptions::new().read(true), limit)?;
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit { return Err(invalid()); }
    Ok(bytes)
}

pub fn text(path: impl AsRef<Path>, limit: u64) -> io::Result<String> {
    String::from_utf8(read(path, limit)?).map_err(|_| invalid())
}

/// Validate before truncating; a linked inode or special file must never receive supervisor writes.
pub fn write(path: impl AsRef<Path>, bytes: &[u8], limit: u64, fresh: bool) -> io::Result<()> {
    if bytes.len() as u64 > limit { return Err(invalid()); }
    let mut options = std::fs::OpenOptions::new();
    options.write(true).mode(0o644);
    if fresh { options.create_new(true); } else { options.create(true).truncate(false); }
    let mut file = open(path.as_ref(), &mut options, limit)?;
    if file.metadata()?.nlink() != 1 { return Err(invalid()); }
    file.set_len(0)?;
    file.write_all(bytes)
}

/// A FIFO regression must fail within a deadline even if a synchronous open/read blocks again.
#[cfg(test)]
pub async fn in_child(name: &str) -> bool {
    if std::env::var("TICO_FILE_TEST_CHILD").as_deref() == Ok(name) { return true; }
    let _guard = crate::gitlocks::TEST_FIXTURES.lock().await;
    let output = tokio::time::timeout(std::time::Duration::from_secs(20),
        tokio::process::Command::new(std::env::current_exe().unwrap()).args(["--exact", name, "--nocapture"])
            .env("TICO_FILE_TEST_CHILD", name).kill_on_drop(true).output()).await.expect("file access blocked").unwrap();
    assert!(output.status.success(), "{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    false
}

#[cfg(test)]
pub fn fifo(path: &Path) {
    use std::os::unix::ffi::OsStrExt;
    let name = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn special_files_and_oversized_files_are_refused_before_reading_or_truncating() {
        if !in_child("regular_file::tests::special_files_and_oversized_files_are_refused_before_reading_or_truncating").await { return; }
        let root = std::env::temp_dir().join(format!("tico-regular-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir(&root).unwrap();
        let path = root.join("file");
        fifo(&path);
        // An open writer with no data catches blocking reads as well as blocking opens.
        let holder = std::fs::OpenOptions::new().read(true).write(true).custom_flags(libc::O_NONBLOCK).open(&path).unwrap();
        assert!(read(&path, SMALL).is_err());
        assert!(write(&path, b"new", SMALL, false).is_err());
        drop(holder);
        std::fs::remove_file(&path).unwrap();
        let target = root.join("target");
        std::fs::write(&target, b"keep").unwrap();
        std::os::unix::fs::symlink(&target, &path).unwrap();
        assert!(read(&path, SMALL).is_err());
        assert!(write(&path, b"new", SMALL, false).is_err());
        assert_eq!(std::fs::read(&target).unwrap(), b"keep");
        assert!(read("/dev/null", SMALL).is_err());
        assert!(write("/dev/null", b"new", SMALL, false).is_err());
        std::fs::remove_file(&path).unwrap();
        std::fs::File::create(&path).unwrap().set_len(SMALL + 1).unwrap();
        assert!(read(&path, SMALL).is_err());
        assert!(write(&path, b"new", SMALL, false).is_err());
        assert_eq!(path.metadata().unwrap().len(), SMALL + 1);
        std::fs::remove_file(&path).unwrap();
        write(&path, b"accepted", SMALL, true).unwrap();
        assert_eq!(read(&path, SMALL).unwrap(), b"accepted");
        std::fs::remove_dir_all(root).unwrap();
    }
}
