//! Where the running Tico Recorder app lives, and whether it can update itself there.
//!
//! macOS runs an app opened straight from Downloads or a disk image from a read-only, randomized copy
//! (App Translocation), and an app on a mounted disk image or in a folder this user cannot write cannot be
//! replaced by a new build. In those places the updater cannot install, so the window offers
//! "Move to Applications": copy the bundle to /Applications (or ~/Applications), drop the quarantine flag
//! that causes translocation, and relaunch from there.
use anyhow::{Context, Result, bail};
use std::{
    cmp::Ordering,
    path::{Path, PathBuf},
    process::Command,
};

/// Why this copy cannot update itself, or `Ok`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Location {
    /// In a folder the updater can replace.
    Ok,
    /// Opened from Downloads or a disk image: macOS runs a read-only copy.
    Translocated,
    /// On a mounted disk image (or another volume under /Volumes).
    DiskImage,
    /// The folder holding the app cannot be written by this user.
    ReadOnly,
}

impl Location {
    /// The short line the Updates row shows; empty when updates work.
    pub fn line(self) -> &'static str {
        match self {
            Location::Ok => "",
            Location::Translocated | Location::DiskImage => "Can't update from here",
            Location::ReadOnly => "Can't update in this folder",
        }
    }

    pub fn can_update(self) -> bool {
        self == Location::Ok
    }
}

/// The `.app` bundle that holds `exe` (…/Tico Recorder.app/Contents/MacOS/tico-recorder), if any.
pub fn bundle_of(exe: &Path) -> Option<PathBuf> {
    exe.ancestors()
        .find(|path| path.extension().is_some_and(|ext| ext == "app"))
        .map(Path::to_path_buf)
}

/// Where the bundle at `bundle` stands. `writable` says whether this user can write a folder.
pub fn check(bundle: &Path, writable: impl Fn(&Path) -> bool) -> Location {
    let text = bundle.to_string_lossy();
    if text.contains("/AppTranslocation/") {
        return Location::Translocated;
    }
    if text.starts_with("/Volumes/") {
        return Location::DiskImage;
    }
    match bundle.parent() {
        Some(folder) if writable(folder) => Location::Ok,
        _ => Location::ReadOnly,
    }
}

/// Whether this user can create files in `folder`: tries one and removes it.
pub fn writable(folder: &Path) -> bool {
    let probe = folder.join(format!(".tico-recorder-write-{}", std::process::id()));
    match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&probe)
    {
        Ok(_) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// Where Move to Applications copies to: /Applications when this user can write it, else ~/Applications.
pub fn target_folder(home: &Path, writable: impl Fn(&Path) -> bool) -> PathBuf {
    let system = Path::new("/Applications");
    if writable(system) {
        system.to_path_buf()
    } else {
        home.join("Applications")
    }
}

/// Compares dotted versions ("0.5.143" < "0.5.150"); anything after `-` or `+` is ignored, a missing part is 0.
pub fn compare_versions(a: &str, b: &str) -> Ordering {
    let parts = |v: &str| -> Vec<u64> {
        v.trim()
            .trim_start_matches('v')
            .split(['-', '+'])
            .next()
            .unwrap_or("")
            .split('.')
            .map(|part| part.parse().unwrap_or(0))
            .collect()
    };
    let (a, b) = (parts(a), parts(b));
    for i in 0..a.len().max(b.len()) {
        let order = a
            .get(i)
            .copied()
            .unwrap_or(0)
            .cmp(&b.get(i).copied().unwrap_or(0));
        if order != Ordering::Equal {
            return order;
        }
    }
    Ordering::Equal
}

/// What to do about a copy already at the target.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Existing {
    /// Nothing there: copy.
    None,
    /// Older or the same version: replace it.
    Replace,
    /// Newer than this copy: ask first (the version found).
    Newer(String),
}

/// `found` is the version of the app already at the target (None when nothing is there; an unreadable
/// version counts as older).
pub fn existing(found: Option<&str>, current: &str) -> Existing {
    match found {
        None => Existing::None,
        Some(version) if compare_versions(version, current) == Ordering::Greater => {
            Existing::Newer(version.to_string())
        }
        Some(_) => Existing::Replace,
    }
}

/// The CFBundleShortVersionString of an app bundle, or "" when it has none.
pub fn bundle_version(bundle: &Path) -> String {
    Command::new("/usr/bin/defaults")
        .arg("read")
        .arg(bundle.join("Contents/Info"))
        .arg("CFBundleShortVersionString")
        .output()
        .ok()
        .filter(|out| out.status.success())
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Copies `bundle` into `folder` (replacing a copy already there), clears the quarantine flag that makes
/// macOS translocate it, and returns the new path. The new copy is staged next to the old one and swapped
/// in, so a failed copy never leaves a half-written app.
pub fn copy_to(bundle: &Path, folder: &Path) -> Result<PathBuf> {
    let name = bundle.file_name().context("The app has no name")?;
    std::fs::create_dir_all(folder).with_context(|| format!("Create {}", folder.display()))?;
    let target = folder.join(name);
    let staged = folder.join(format!(".{}.new", name.to_string_lossy()));
    let _ = std::fs::remove_dir_all(&staged);
    let copied = Command::new("/usr/bin/ditto")
        .arg("--noqtn")
        .arg(bundle)
        .arg(&staged)
        .status()
        .context("Run ditto")?;
    if !copied.success() {
        let _ = std::fs::remove_dir_all(&staged);
        bail!("The app could not be copied");
    }
    // `--noqtn` skips the quarantine flag; this clears any that came along anyway.
    let _ = Command::new("/usr/bin/xattr")
        .args(["-dr", "com.apple.quarantine"])
        .arg(&staged)
        .status();
    if target.exists() {
        std::fs::remove_dir_all(&target)
            .with_context(|| format!("Remove the old {}", target.display()))?;
    }
    std::fs::rename(&staged, &target).context("Move the new copy in place")?;
    Ok(target)
}

/// Opens `bundle` once this process (`pid`) has exited, so the new copy can take the library lock.
pub fn open_after_exit(bundle: &Path, pid: u32) -> Result<()> {
    Command::new("/bin/sh")
        .arg("-c")
        .arg(r#"while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec /usr/bin/open "$2""#)
        .arg("sh")
        .arg(pid.to_string())
        .arg(bundle)
        .spawn()
        .context("Start the relaunch")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const APP: &str = "Tico Recorder.app";

    fn yes(_: &Path) -> bool {
        true
    }

    #[test]
    fn translocated_copies_cannot_update() {
        let path =
            Path::new("/private/var/folders/x1/abc/T/AppTranslocation/1F2E-AB/d/Tico Recorder.app");
        assert_eq!(check(path, yes), Location::Translocated);
        assert!(!check(path, yes).can_update());
        assert_eq!(check(path, yes).line(), "Can't update from here");
    }

    #[test]
    fn disk_images_cannot_update() {
        let path = Path::new("/Volumes/Tico Recorder/Tico Recorder.app");
        assert_eq!(check(path, yes), Location::DiskImage);
    }

    #[test]
    fn applications_folders_update() {
        assert_eq!(
            check(&Path::new("/Applications").join(APP), yes),
            Location::Ok
        );
        assert_eq!(
            check(&Path::new("/Users/dana/Applications").join(APP), yes),
            Location::Ok
        );
        assert_eq!(Location::Ok.line(), "");
    }

    #[test]
    fn unwritable_folders_cannot_update() {
        let path = Path::new("/Applications").join(APP);
        assert_eq!(check(&path, |_| false), Location::ReadOnly);
        assert_eq!(Location::ReadOnly.line(), "Can't update in this folder");
    }

    #[test]
    fn the_write_check_is_real_and_leaves_nothing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(writable(dir.path()));
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
        assert!(!writable(&dir.path().join("missing")));
    }

    #[test]
    fn finds_the_bundle_from_the_binary() {
        let exe = Path::new("/Applications/Tico Recorder.app/Contents/MacOS/tico-recorder");
        assert_eq!(
            bundle_of(exe).as_deref(),
            Some(Path::new("/Applications/Tico Recorder.app"))
        );
        assert_eq!(bundle_of(Path::new("/usr/local/bin/tico-recorder")), None);
    }

    #[test]
    fn moves_to_user_applications_when_the_system_folder_is_locked() {
        let home = Path::new("/Users/dana");
        assert_eq!(target_folder(home, yes), Path::new("/Applications"));
        assert_eq!(
            target_folder(home, |_| false),
            Path::new("/Users/dana/Applications")
        );
    }

    #[test]
    fn versions_compare_by_number() {
        assert_eq!(compare_versions("0.5.143", "0.5.150"), Ordering::Less);
        assert_eq!(compare_versions("0.5.150", "0.5.143"), Ordering::Greater);
        assert_eq!(compare_versions("0.5.143", "0.5.143"), Ordering::Equal);
        assert_eq!(compare_versions("v0.6", "0.6.0"), Ordering::Equal);
        assert_eq!(compare_versions("0.10.0", "0.9.9"), Ordering::Greater);
        assert_eq!(compare_versions("0.6.0-beta", "0.6.0"), Ordering::Equal);
    }

    #[test]
    fn replaces_older_or_same_and_asks_about_newer() {
        assert_eq!(existing(None, "0.5.143"), Existing::None);
        assert_eq!(existing(Some("0.5.100"), "0.5.143"), Existing::Replace);
        assert_eq!(existing(Some("0.5.143"), "0.5.143"), Existing::Replace);
        assert_eq!(existing(Some(""), "0.5.143"), Existing::Replace);
        assert_eq!(
            existing(Some("0.5.150"), "0.5.143"),
            Existing::Newer("0.5.150".into())
        );
    }
}
