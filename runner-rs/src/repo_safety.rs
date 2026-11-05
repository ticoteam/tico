//! A bot's checkout must stay outside the Tico installation, including through linked ancestors.
use std::path::{Component, Path, PathBuf};
use std::os::unix::fs::MetadataExt;

/// Resolve existing ancestors too, so a not-yet-created checkout has the same boundary as an existing one.
fn resolve(path: &Path) -> Result<PathBuf, std::io::Error> {
    let absolute = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir()?.join(path) };
    match absolute.canonicalize() {
        Ok(real) => return Ok(real),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let mut parent = absolute.as_path();
    let mut tail = Vec::new();
    loop {
        match std::fs::symlink_metadata(parent) {
            Ok(_) => break,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        let name = parent.components().next_back().ok_or_else(|| std::io::Error::other("missing path"))?;
        tail.push(name);
        parent = parent.parent().ok_or_else(|| std::io::Error::other("missing parent"))?;
    }
    // An unresolved '..' can hide a later link into the install; do not append it unchecked.
    if tail.contains(&Component::ParentDir) {
        return Err(std::io::Error::other("unresolved parent component"));
    }
    let mut real = if std::fs::symlink_metadata(parent)?.file_type().is_symlink() {
        resolve(&parent.parent().ok_or_else(|| std::io::Error::other("missing parent"))?.join(std::fs::read_link(parent)?))?
    } else {
        parent.canonicalize()?
    };
    for part in tail.into_iter().rev() {
        real.push(part);
    }
    Ok(real)
}

pub fn outside_installation(path: &Path) -> Result<PathBuf, String> {
    outside(path, &crate::env::root())
}

fn outside(path: &Path, installation: &Path) -> Result<PathBuf, String> {
    let unchecked = |_| format!("Cannot check repository path: {}", path.display());
    let real = resolve(path).map_err(unchecked)?;
    let installation = resolve(installation).map_err(unchecked)?;
    let identity = std::fs::metadata(&installation).map_err(unchecked)?;
    let alias = real.ancestors().filter_map(|p| std::fs::metadata(p).ok())
        .any(|m| m.dev() == identity.dev() && m.ino() == identity.ino());
    if real.starts_with(installation) || alias {
        return Err(format!("Repository path is inside the Tico install: {}", path.display()));
    }
    Ok(real)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn bot_roots_and_new_checkouts_cannot_reach_the_install_through_links() {
        let dir = std::env::temp_dir().join(format!("tico-repo-safety-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let dir = dir.canonicalize().unwrap();
        let install = dir.join("install");
        std::fs::create_dir_all(install.join("templates")).unwrap();
        std::fs::create_dir_all(dir.join("ws/existing")).unwrap();
        symlink(install.join("templates"), dir.join("ws/link")).unwrap();
        symlink(&install, dir.join("bot-ops")).unwrap();
        symlink(&dir, dir.join("ancestor")).unwrap();
        for path in [install.clone(), install.join("templates"), dir.join("bot-ops"),
                     dir.join("ancestor/install/templates"), dir.join("bot-ops/new/../checkout"),
                     dir.join("ws/link/new"), dir.join("ws/missing/../link/new"),
                     dir.join("ws/missing/deep/../../link/new"), dir.join("ws/existing/../link/new")] {
            assert!(outside(&path, &install).unwrap_err().contains(&path.display().to_string()));
        }
        assert!(!dir.join("ws/missing").exists());
        assert_eq!(outside(&dir.join("ws/safe/new"), &install).unwrap(), dir.join("ws/safe/new"));
        assert_eq!(outside(&dir.join("install-other/new"), &install).unwrap(), dir.join("install-other/new"));
        let other_case = dir.join("INSTALL");
        if other_case.exists() {
            assert!(outside(&other_case.join("new"), &install).is_err());
        }
        std::fs::remove_dir_all(dir).unwrap();
    }
}
