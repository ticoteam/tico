//! A bot's checkout must stay outside the Tico installation, including through linked ancestors.
use std::path::{Component, Path, PathBuf};
use std::os::unix::fs::MetadataExt;

fn git_identity(path: &Path) -> Result<Vec<PathBuf>, String> {
    let unchecked = || format!("Cannot check repository identity: {}", path.display());
    let mut existing = path;
    while !existing.exists() {
        existing = existing.parent().ok_or_else(unchecked)?;
    }
    if !existing.is_dir() {
        existing = existing.parent().ok_or_else(unchecked)?;
    }
    let out = std::process::Command::new("git")
        .args(crate::safegit::args(existing))
        .args(["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"])
        .env_clear().envs(crate::safegit::clean_environment(&crate::env::process_environment()))
        .stdin(std::process::Stdio::null()).output().map_err(|_| unchecked())?;
    if !out.status.success() {
        // Missing destinations are fine; existing but unreadable metadata is not.
        if existing.ancestors().any(|p| p.join(".git").symlink_metadata().is_ok()
            || (p.join("HEAD").exists() && p.join("objects").is_dir())) {
            return Err(unchecked());
        }
        return Ok(vec![]);
    }
    let text = std::str::from_utf8(&out.stdout).map_err(|_| unchecked())?;
    if text.lines().count() != 2 || text.lines().any(|p| !Path::new(p).is_absolute()) {
        return Err(unchecked());
    }
    text.lines().map(|p| resolve(Path::new(p)).map_err(|_| unchecked())).collect()
}

fn same_path(left: &Path, right: &Path) -> bool {
    left == right || std::fs::metadata(left).ok().zip(std::fs::metadata(right).ok())
        .is_some_and(|(a, b)| a.dev() == b.dev() && a.ino() == b.ino())
}

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
    if real.starts_with(&installation) || alias {
        return Err(format!("Repository path is inside the Tico install: {}", path.display()));
    }
    let installation_git = git_identity(&installation)?;
    if git_identity(&real)?.iter().any(|a| installation_git.iter().any(|b| same_path(a, b))) {
        return Err(format!("Repository shares Git metadata with the Tico install: {}", path.display()));
    }
    Ok(real)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn git(root: &Path, args: &[&str]) -> String {
        let out = std::process::Command::new("git").arg("-C").arg(root).args(args)
            .env_clear().envs(crate::safegit::clean_environment(&crate::env::process_environment()))
            .output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    }

    #[test]
    fn worktrees_and_git_metadata_aliases_of_regular_and_linked_installs_are_refused() {
        for linked in [false, true] {
            let dir = std::env::temp_dir().join(format!("tico-repo-identity-{}", uuid::Uuid::new_v4()));
            let source = dir.join("source");
            std::fs::create_dir_all(&source).unwrap();
            let dir = dir.canonicalize().unwrap();
            let source = dir.join("source");
            git(&source, &["init", "-q", "-b", "main"]);
            git(&source, &["-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "--allow-empty", "-qm", "init"]);
            let install = if linked {
                let install = dir.join("install");
                git(&source, &["worktree", "add", "--detach", install.to_str().unwrap()]);
                install
            } else { source.clone() };
            let sibling = dir.join("task");
            git(&source, &["worktree", "add", "--detach", sibling.to_str().unwrap()]);
            let alias = dir.join("alias");
            std::fs::create_dir(&alias).unwrap();
            std::fs::write(alias.join(".git"), format!("gitdir: {}\n", git(&install, &["rev-parse", "--absolute-git-dir"]))).unwrap();
            let common = dir.join("common");
            std::fs::create_dir(&common).unwrap();
            git(&common, &["init", "-q"]);
            std::fs::write(common.join(".git/commondir"), format!("{}\n", source.join(".git").display())).unwrap();
            for path in [sibling.clone(), sibling.join("new/deep"), alias, common] {
                assert!(outside(&path, &install).unwrap_err().contains("shares Git metadata"), "{}", path.display());
            }
            let separate = dir.join("separate");
            git(&dir, &["clone", "-q", source.to_str().unwrap(), separate.to_str().unwrap()]);
            assert_eq!(outside(&separate, &install).unwrap(), separate);
            assert_eq!(outside(&dir.join("new"), &install).unwrap(), dir.join("new"));
            std::fs::remove_dir_all(dir).unwrap();
        }
    }

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
