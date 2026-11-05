//! A bot's checkout must stay outside the Tico installation, including through linked ancestors.
use std::path::{Component, Path, PathBuf};

/// Resolve existing ancestors too, so a not-yet-created checkout has the same boundary as an existing one.
fn resolve(path: &Path) -> Result<PathBuf, std::io::Error> {
    let absolute = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir()?.join(path) };
    if let Ok(real) = absolute.canonicalize() {
        return Ok(real);
    }
    let mut parent = absolute.as_path();
    let mut tail = Vec::new();
    while std::fs::symlink_metadata(parent).is_err() {
        let name = parent.components().next_back().ok_or_else(|| std::io::Error::other("missing path"))?;
        tail.push(name);
        parent = parent.parent().ok_or_else(|| std::io::Error::other("missing parent"))?;
    }
    let mut real = parent.canonicalize()?;
    for part in tail.into_iter().rev() {
        match part {
            Component::ParentDir => { real.pop(); }
            Component::CurDir => {}
            _ => real.push(part),
        }
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
    if real.starts_with(installation) {
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
        symlink(&install, dir.join("bot-ops")).unwrap();
        symlink(&dir, dir.join("ancestor")).unwrap();
        for path in [install.clone(), install.join("templates"), dir.join("bot-ops"),
                     dir.join("ancestor/install/templates"), dir.join("bot-ops/new/../checkout")] {
            assert!(outside(&path, &install).unwrap_err().contains(&path.display().to_string()));
        }
        assert_eq!(outside(&dir.join("install-other/new"), &install).unwrap(), dir.join("install-other/new"));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
