//! Trusted input custody for a nightly tidy check. Only a pinned upstream tree is
//! read; neither model sees the checkout, Hub credentials, or its ordinary prompt.
use crate::client::Client;
use serde_json::json;
use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

const LIMIT: usize = 64_000_000;

async fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    let mut command = crate::isolation::command("git");
    command
        .args(crate::safegit::args(root))
        .args(args)
        .env_clear()
        .envs(crate::safegit::clean_environment(&crate::env::process_environment()))
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    let output = tokio::time::timeout(std::time::Duration::from_secs(30), command.output())
        .await
        .map_err(|_| "Nightly memory tidy: git timed out")?
        .map_err(|_| "Nightly memory tidy: git failed")?;
    if !output.status.success() || output.stdout.len() > LIMIT {
        return Err("Nightly memory tidy: pushed snapshot unavailable".into());
    }
    String::from_utf8(output.stdout).map_err(|_| "Nightly memory tidy: snapshot is not UTF-8".into())
}

pub struct Packet {
    pub workspace: PathBuf,
    pub prompt: String,
    pub checkout: PathBuf,
}

fn walk(root: &Path, path: &Path, entries: &mut BTreeMap<String, String>, bytes: &mut usize) -> Result<(), String> {
    let metadata = std::fs::symlink_metadata(path).map_err(|_| "Nightly memory tidy: manifest read failed")?;
    let name = path
        .strip_prefix(root)
        .map_err(|_| "Nightly memory tidy: invalid manifest path")?
        .to_str()
        .ok_or("Nightly memory tidy: non-UTF-8 path")?
        .to_string();
    if name == ".git" || name.starts_with(".git/") {
        return Ok(());
    }
    if entries.len() >= 10000 {
        return Err("Nightly memory tidy: manifest has too many paths".into());
    }
    let mode = metadata.permissions().mode();
    let value = if metadata.file_type().is_symlink() {
        format!(
            "link:{mode}:{}",
            std::fs::read_link(path)
                .map_err(|_| "Nightly memory tidy: link read failed")?
                .display()
        )
    } else if metadata.is_dir() {
        for child in std::fs::read_dir(path).map_err(|_| "Nightly memory tidy: directory read failed")? {
            walk(
                root,
                &child.map_err(|_| "Nightly memory tidy: directory read failed")?.path(),
                entries,
                bytes,
            )?;
        }
        format!("dir:{mode}")
    } else if metadata.is_file() {
        *bytes = bytes
            .checked_add(metadata.len() as usize)
            .ok_or("Nightly memory tidy: manifest size overflow")?;
        if *bytes > LIMIT {
            return Err("Nightly memory tidy: checkout exceeds manifest limit".into());
        }
        let data = std::fs::read(path).map_err(|_| "Nightly memory tidy: manifest file read failed")?;
        format!("file:{mode}:{}", crate::gitrun::sha256_hex(&data))
    } else {
        return Err("Nightly memory tidy: unsupported checkout file".into());
    };
    entries.insert(name, value);
    Ok(())
}

async fn manifest(checkout: &Path, workspace: &Path) -> Result<String, String> {
    let mut all = BTreeMap::new();
    for (prefix, root) in [("checkout", checkout), ("packet", workspace)] {
        let mut entries = BTreeMap::new();
        walk(root, root, &mut entries, &mut 0)?;
        for (path, value) in entries {
            all.insert(format!("{prefix}/{path}"), value);
        }
    }
    // Git internals are not traversed, but a commit or checkout move must count.
    all.insert("git-head".into(), git(checkout, &["rev-parse", "HEAD"]).await?);
    Ok(crate::gitrun::sha256_hex(
        &serde_json::to_vec(&all).map_err(|_| "Nightly memory tidy: invalid manifest")?,
    ))
}

impl Packet {
    pub async fn prepare(client: &Client, source: &Path, checkout: &Path, directory: &Path, aid: &str) -> Result<Self, String> {
        let commit = git(source, &["rev-parse", "--verify", "@{u}"]).await?.trim().to_string();
        if ![40, 64].contains(&commit.len()) || !commit.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("Nightly memory tidy: no pinned pushed commit".into());
        }
        let tree = git(source, &["ls-tree", "-rz", &commit, "--", "AGENT.md", "memory", "knowledge"]).await?;
        let mut snapshot = BTreeMap::new();
        let mut size = 0;
        for entry in tree.split('\0').filter(|e| !e.is_empty()) {
            let (object, path) = entry.split_once('\t').ok_or("Nightly memory tidy: invalid pushed tree")?;
            if path != "AGENT.md" && !path.ends_with(".md") {
                continue;
            }
            let parts: Vec<&str> = object.split_whitespace().collect();
            if parts.len() != 3 || parts[1] != "blob" || !["100644", "100755"].contains(&parts[0]) {
                return Err("Nightly memory tidy: unsupported snapshot entry".into());
            }
            let content = git(source, &["cat-file", "blob", parts[2]]).await?;
            size += content.len() + path.len();
            if size > LIMIT || snapshot.len() >= 10000 {
                return Err("Nightly memory tidy: snapshot exceeds limit".into());
            }
            snapshot.insert(path, content);
        }
        let workspace = directory.join("memory-tidy").join(aid);
        std::fs::create_dir_all(&workspace).map_err(|_| "Nightly memory tidy: cannot prepare empty workspace")?;
        let before = manifest(checkout, &workspace).await?;
        let result = client
            .post(
                &format!("attempts/{aid}/memory-tidy/prepare"),
                &json!({"memory_commit": commit, "snapshot": snapshot, "manifest": before}),
                Some(&format!("tidy-prepare:{aid}")),
            )
            .await
            .map_err(|e| e.detail)?;
        if !result["packet"].is_object() {
            return Err("Nightly memory tidy: admission withdrawn before model call".into());
        }
        let prompt = serde_json::to_string(&result["packet"]).map_err(|_| "Nightly memory tidy: invalid packet")?;
        Ok(Self {
            workspace,
            prompt,
            checkout: checkout.into(),
        })
    }

    pub async fn propose(&self, client: &Client, aid: &str, reply: &str) -> Result<Option<String>, String> {
        let result = client
            .post(
                &format!("attempts/{aid}/memory-tidy/proposal"),
                &json!({"text": reply}),
                Some(&format!("tidy-proposal:{aid}")),
            )
            .await
            .map_err(|e| e.detail)?;
        if result["verification"].is_null() {
            return Ok(None);
        }
        serde_json::to_string(&result["verification"])
            .map(Some)
            .map_err(|_| "Nightly memory tidy: invalid verification packet".into())
    }

    pub async fn verify(&self, client: &Client, aid: &str, reply: &str) -> Result<(), String> {
        let after = manifest(&self.checkout, &self.workspace).await?;
        client
            .post(
                &format!("attempts/{aid}/memory-tidy/verification"),
                &json!({"text": reply, "manifest": after}),
                Some(&format!("tidy-verification:{aid}")),
            )
            .await
            .map_err(|e| e.detail)?;
        Ok(())
    }
}

impl Drop for Packet {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.workspace);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_covers_untracked_files_modes_and_symlinks_without_following_them() {
        let root = std::env::temp_dir().join(format!("tico-tidy-fixture-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("untracked");
        std::fs::write(&file, "synthetic before").unwrap();
        let read = || {
            let mut entries = BTreeMap::new();
            walk(&root, &root, &mut entries, &mut 0).unwrap();
            entries
        };
        let before = read();
        std::fs::write(&file, "synthetic after").unwrap();
        assert_ne!(before, read());
        let before = read();
        std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert_ne!(before, read());
        std::os::unix::fs::symlink("/does/not/exist", root.join("link")).unwrap();
        assert!(read()["link"].contains("/does/not/exist"));
        std::fs::remove_dir_all(&root).unwrap();
    }
}
