//! The files a bot's own checkout and its AI tools' homes hold, read, listed and written as the bot, never by the
//! runner itself: the checkout's instruction file (crate::instructions) and the Gemini CLI's saved memory, which is
//! set aside before each turn (crate::hosts::gemini::set_aside_memory). These folders are the bot's to change, so a
//! read, a write or a removal runs as the bot (crate::isolation): a link the bot planted reaches only what the bot
//! could, and a write replaces a link at the leaf rather than writing through it.

use std::path::Path;

fn as_bot(
    script: &str,
    args: &[&Path],
    input: Option<&str>,
) -> std::io::Result<std::process::Output> {
    use std::io::Write;
    let mut cmd = crate::isolation::std_command("/bin/sh");
    cmd.arg("-c")
        .arg(script)
        .arg("sh")
        .args(args)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .stdin(if input.is_some() {
            std::process::Stdio::piped()
        } else {
            std::process::Stdio::null()
        })
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    let mut child = cmd.spawn()?;
    if let Some(text) = input {
        child
            .stdin
            .take()
            .ok_or_else(|| std::io::Error::other("no stdin"))?
            .write_all(text.as_bytes())?;
    }
    child.wait_with_output()
}

fn failed(what: &str, path: &Path, out: &std::process::Output) -> std::io::Error {
    std::io::Error::other(format!(
        "{what} {}: {}",
        path.display(),
        String::from_utf8_lossy(&out.stderr).trim()
    ))
}

/// A file's text, read as the bot: None when it is missing or is a link (never read through one).
pub fn bot_read(path: &Path) -> std::io::Result<Option<String>> {
    let out = as_bot(
        r#"if [ -L "$1" ] || [ ! -e "$1" ]; then exit 3; fi; exec cat -- "$1""#,
        &[path],
        None,
    )?;
    match out.status.code() {
        Some(0) => String::from_utf8(out.stdout).map(Some).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("{} is not text", path.display()),
            )
        }),
        Some(3) => Ok(None),
        _ => Err(failed("could not read", path, &out)),
    }
}

/// Replaces a file with `text` as the bot: a new file beside it, renamed over it, so a link there is replaced and
/// never written through, and a reader sees the old text or the new, never part of it.
pub fn bot_write(path: &Path, text: &str) -> std::io::Result<()> {
    let out = as_bot(
        r#"set -e; d=$(dirname -- "$1"); mkdir -p -- "$d"; if [ -d "$1" ]; then echo "is a folder" >&2; exit 4; fi
t=$(mktemp "$d/.tico-memory.XXXXXX"); trap 'rm -f -- "$t"' EXIT; cat > "$t"; mv -f -- "$t" "$1""#,
        &[path],
        Some(text),
    )?;
    if out.status.success() {
        Ok(())
    } else {
        Err(failed("could not write", path, &out))
    }
}

/// Removes a file (a link itself, not what it names) as the bot.
pub fn bot_remove(path: &Path) -> std::io::Result<()> {
    let out = as_bot(r#"rm -f -- "$1""#, &[path], None)?;
    if out.status.success() {
        Ok(())
    } else {
        Err(failed("could not remove", path, &out))
    }
}

/// True when a path is a git checkout, a task worktree's `.git` file included.
pub fn is_checkout(path: &Path) -> bool {
    path.join(".git").exists()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("bot-files-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A write replaces a link that appears at the leaf; it never writes through it, and a read of a link is None.
    #[test]
    fn links_are_never_followed_for_a_write_or_a_read() {
        let root = temp();
        let outside = root.join("supervisor");
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("runner.json"), "SECRET-TOKEN\n").unwrap();
        let leaf = root.join("leaf.md");
        std::os::unix::fs::symlink(outside.join("runner.json"), &leaf).unwrap();
        assert_eq!(bot_read(&leaf).unwrap(), None);
        bot_write(&leaf, "mine\n").unwrap();
        assert_eq!(
            std::fs::read_to_string(outside.join("runner.json")).unwrap(),
            "SECRET-TOKEN\n"
        );
        assert!(!std::fs::symlink_metadata(&leaf)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(bot_read(&leaf).unwrap().as_deref(), Some("mine\n"));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn only_a_git_checkout_is_a_checkout() {
        let root = temp();
        assert!(!is_checkout(&root));
        std::fs::write(root.join(".git"), "gitdir: /elsewhere/.git/worktrees/t\n").unwrap();
        assert!(is_checkout(&root), "a task worktree's .git is a file");
        std::fs::remove_dir_all(root).unwrap();
    }
}
