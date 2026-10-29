//! Google Chrome and chrome-devtools-mcp, installed on first need under the runner's tools directory (the
//! runner-home volume on Docker), never in the image. Both are pinned with a checksum. Google removes a stable
//! build from its repository some weeks after the next one ships; then the current stable is taken with the
//! checksum Google's repository index gives for it (fetched over TLS), and the log says so.

use std::path::{Path, PathBuf};
use std::time::Duration;

use futures_util::StreamExt;
use sha2::Digest;
use tokio::io::AsyncWriteExt;

use crate::util::{diagnostic, log};

const REPO: &str = "https://dl.google.com/linux/chrome/deb";
/// (Debian architecture, version, size, sha256) of the pinned google-chrome-stable package.
const CHROME_PINS: &[(&str, &str, u64, &str)] = &[
    ("amd64", "156.0.8078.79-1", 145_633_696, "be1a1e161419f5c8272432f6860912550d8e29985e925913fa800493a0d0c955"),
    ("arm64", "156.0.8078.79-1", 137_373_656, "dc66f2d96853153e350c4a0944da4503cae39c2fcbcdb4e0057c64f6c49202ff"),
];
/// chrome-devtools-mcp: 1.11.0 is the first release whose bundled Puppeteer keeps the path of `--browserUrl`
/// (the per-bot secret lives there). Its npm package has no dependencies, so one tarball is the whole install.
pub const MCP_VERSION: &str = "1.11.0";
const MCP_SHA256: &str = "e9f0ab97ad9515927c7d9f0a3dfbdb9ea485d2810d572e4a64eb84b192c8bf0c";
const MCP_ENTRY: &str = "build/src/bin/chrome-devtools-mcp.js";
const MARKER: &str = ".tico-installed";
const DOWNLOAD_LIMIT: u64 = 400 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct Chrome {
    pub exe: PathBuf,
    pub version: String,
}

fn arch() -> Option<&'static str> {
    match std::env::consts::ARCH {
        "x86_64" => Some("amd64"),
        "aarch64" => Some("arm64"),
        _ => None,
    }
}

/// The Chrome already installed under `tools`, if any: the pinned version, else the newest one there.
pub fn installed(tools: &Path) -> Option<Chrome> {
    let root = tools.join("chrome");
    let mut found: Vec<(Vec<u64>, String)> = std::fs::read_dir(&root)
        .ok()?
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|v| !v.starts_with('.') && root.join(v).join(MARKER).is_file() && root.join(v).join("chrome").is_file())
        .map(|v| (crate::harness_tools::version_key(&v), v))
        .collect();
    found.sort();
    let pinned = arch().and_then(|a| CHROME_PINS.iter().find(|p| p.0 == a)).map(|p| p.1.to_string());
    let pick = found.iter().find(|(_, v)| Some(v) == pinned.as_ref()).or(found.last())?.1.clone();
    Some(Chrome { exe: root.join(&pick).join("chrome"), version: pick })
}

/// Install the pinned Chrome (once; later calls find it). Takes a few minutes on a slow line: about 140 MB down,
/// 450 MB on disk.
pub async fn chrome(tools: &Path) -> Result<Chrome, String> {
    let Some(arch) = arch() else { return Err(format!("Chrome is not published for {}", std::env::consts::ARCH)) };
    let (_, pinned, size, sha) = *CHROME_PINS.iter().find(|p| p.0 == arch).unwrap();
    if let Some(c) = installed(tools).filter(|c| c.version == pinned) {
        return Ok(c);
    }
    if crate::env::which("dpkg-deb", &std::env::var("PATH").unwrap_or_default()).is_none() {
        return Err("dpkg-deb is not installed on this computer, and installing Chrome needs it".into());
    }
    let root = tools.join("chrome");
    std::fs::create_dir_all(&root).map_err(|e| format!("cannot make {} ({e})", root.display()))?;
    let deb = root.join(format!(".download-{}.deb", uuid::Uuid::new_v4().simple()));
    let file = format!("pool/main/g/google-chrome-stable/google-chrome-stable_{pinned}_{arch}.deb");
    let (version, result) = match download(&format!("{REPO}/{file}"), &deb, size, sha).await {
        Err(Fetch::Gone) => {
            let (version, path, size, sha) = current_stable(arch).await?;
            diagnostic(&format!(
                "Tico runner: Chrome {pinned} is no longer in Google's repository; installing the current stable {version} (checksum from the repository index)"
            ));
            (version, download(&format!("{REPO}/{path}"), &deb, size, &sha).await)
        }
        other => (pinned.to_string(), other),
    };
    let unpacked = match result {
        Ok(()) => unpack_deb(&deb, &root, &version).await,
        Err(e) => Err(e.text()),
    };
    let _ = std::fs::remove_file(&deb);
    let chrome = unpacked?;
    // Older versions go once the new one is in place: nothing runs them (a runner update restarts every Chrome).
    for entry in std::fs::read_dir(&root).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name != version && !name.starts_with(".download-") {
            let _ = std::fs::remove_dir_all(entry.path());
        }
    }
    log(&format!("Tico runner: Chrome {version} installed for bots' browsers"));
    Ok(chrome)
}

async fn unpack_deb(deb: &Path, root: &Path, version: &str) -> Result<Chrome, String> {
    let scratch = root.join(format!(".unpack-{}", uuid::Uuid::new_v4().simple()));
    let result = async {
        let out = tokio::process::Command::new("dpkg-deb")
            .arg("-x")
            .arg(deb)
            .arg(&scratch)
            .stdin(std::process::Stdio::null())
            .output()
            .await
            .map_err(|e| format!("dpkg-deb did not run ({e})"))?;
        if !out.status.success() {
            return Err(format!("dpkg-deb could not unpack Chrome: {}", String::from_utf8_lossy(&out.stderr).trim()));
        }
        let dest = root.join(version);
        let _ = std::fs::remove_dir_all(&dest);
        std::fs::rename(scratch.join("opt/google/chrome"), &dest).map_err(|e| format!("cannot place Chrome ({e})"))?;
        // The setuid sandbox helper is never used (bots' Chrome uses the namespace sandbox or none) and must not be
        // left setuid where the supervisor runs as root.
        let _ = std::fs::remove_file(dest.join("chrome-sandbox"));
        open_tree(&dest);
        std::fs::write(dest.join(MARKER), version).map_err(|e| e.to_string())?;
        Ok(Chrome { exe: dest.join("chrome"), version: version.to_string() })
    }
    .await;
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

/// Every directory readable and searchable by the bot user, which runs what is inside (dpkg-deb leaves 0750).
fn open_tree(dir: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o755));
    for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            open_tree(&entry.path());
        }
    }
}

/// The current google-chrome-stable from the repository's package index: (version, path, size, sha256).
async fn current_stable(arch: &str) -> Result<(String, String, u64, String), String> {
    let url = format!("{REPO}/dists/stable/main/binary-{arch}/Packages");
    let http = crate::client::http_client(Some(Duration::from_secs(60)));
    let reply = http.get(&url).header("User-Agent", "tico-runner").send().await.map_err(|e| format!("Chrome's package index did not download ({e})"))?;
    if !reply.status().is_success() {
        return Err(format!("Chrome's package index answered HTTP {}", reply.status().as_u16()));
    }
    let text = reply.text().await.map_err(|e| e.to_string())?;
    stanza(&text).ok_or_else(|| "Chrome's package index lists no google-chrome-stable".into())
}

/// The google-chrome-stable entry of a Debian `Packages` index.
fn stanza(index: &str) -> Option<(String, String, u64, String)> {
    for block in index.split("\n\n") {
        let field = |name: &str| block.lines().find_map(|l| l.strip_prefix(&format!("{name}: ")).map(|v| v.trim().to_string()));
        if field("Package").as_deref() != Some("google-chrome-stable") {
            continue;
        }
        let path = field("Filename")?;
        let sha = field("SHA256")?.to_ascii_lowercase();
        let safe = path.starts_with("pool/") && !path.contains("..") && path.ends_with(".deb");
        if !safe || sha.len() != 64 || !sha.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        return Some((field("Version")?, path, field("Size")?.parse().ok()?, sha));
    }
    None
}

enum Fetch {
    Gone,
    Failed(String),
}

impl Fetch {
    fn text(self) -> String {
        match self {
            Fetch::Gone => "the download is no longer published".into(),
            Fetch::Failed(e) => e,
        }
    }
}

/// `url` into `dest`, streamed, checked against its size and sha256 before it counts.
async fn download(url: &str, dest: &Path, size: u64, sha256: &str) -> Result<(), Fetch> {
    let http = crate::client::http_client(Some(Duration::from_secs(1800)));
    let reply = http.get(url).header("User-Agent", "tico-runner").send().await.map_err(|e| Fetch::Failed(format!("download failed ({e})")))?;
    match reply.status().as_u16() {
        404 | 410 => return Err(Fetch::Gone),
        s if !(200..300).contains(&s) => return Err(Fetch::Failed(format!("download answered HTTP {s}"))),
        _ => {}
    }
    let mut out = tokio::fs::File::create(dest).await.map_err(|e| Fetch::Failed(e.to_string()))?;
    let mut hash = sha2::Sha256::new();
    let mut got = 0u64;
    let mut body = reply.bytes_stream();
    while let Some(chunk) = body.next().await {
        let chunk = chunk.map_err(|e| Fetch::Failed(format!("download failed ({e})")))?;
        got += chunk.len() as u64;
        if got > size.clamp(1, DOWNLOAD_LIMIT) {
            return Err(Fetch::Failed("the download is larger than published".into()));
        }
        hash.update(&chunk);
        out.write_all(&chunk).await.map_err(|e| Fetch::Failed(e.to_string()))?;
    }
    out.flush().await.map_err(|e| Fetch::Failed(e.to_string()))?;
    if got != size || hex::encode(hash.finalize()) != sha256 {
        return Err(Fetch::Failed("the download does not match its pinned checksum".into()));
    }
    Ok(())
}

/// chrome-devtools-mcp's entry script, installed when missing. Needs `node` (checked by the caller).
pub async fn devtools_mcp(tools: &Path) -> Result<PathBuf, String> {
    let dest = tools.join("chrome-devtools-mcp").join(MCP_VERSION);
    if dest.join(MARKER).is_file() && dest.join(MCP_ENTRY).is_file() {
        return Ok(dest.join(MCP_ENTRY));
    }
    let root = tools.join("chrome-devtools-mcp");
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let tgz = root.join(format!(".download-{}.tgz", uuid::Uuid::new_v4().simple()));
    let scratch = root.join(format!(".unpack-{}", uuid::Uuid::new_v4().simple()));
    let url = format!("https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-{MCP_VERSION}.tgz");
    let result = async {
        download_any(&url, &tgz, MCP_SHA256).await?;
        std::fs::create_dir(&scratch).map_err(|e| e.to_string())?;
        let out = tokio::process::Command::new("tar")
            .arg("-xzf")
            .arg(&tgz)
            .arg("-C")
            .arg(&scratch)
            .arg("--no-same-owner")
            .arg("--no-same-permissions")
            .stdin(std::process::Stdio::null())
            .output()
            .await
            .map_err(|e| format!("tar did not run ({e})"))?;
        if !out.status.success() {
            return Err(format!("tar could not unpack chrome-devtools-mcp: {}", String::from_utf8_lossy(&out.stderr).trim()));
        }
        let _ = std::fs::remove_dir_all(&dest);
        std::fs::rename(scratch.join("package"), &dest).map_err(|e| e.to_string())?;
        open_tree(&dest);
        std::fs::write(dest.join(MARKER), MCP_VERSION).map_err(|e| e.to_string())?;
        for entry in std::fs::read_dir(&root).into_iter().flatten().flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name != MCP_VERSION && !name.starts_with('.') {
                let _ = std::fs::remove_dir_all(entry.path());
            }
        }
        log(&format!("Tico runner: chrome-devtools-mcp {MCP_VERSION} installed for bots' browsers"));
        Ok(dest.join(MCP_ENTRY))
    }
    .await;
    let _ = std::fs::remove_file(&tgz);
    let _ = std::fs::remove_dir_all(&scratch);
    result
}

async fn download_any(url: &str, dest: &Path, sha256: &str) -> Result<(), String> {
    let http = crate::client::http_client(Some(Duration::from_secs(300)));
    let reply = http.get(url).header("User-Agent", "tico-runner").send().await.map_err(|e| format!("download failed ({e})"))?;
    if !reply.status().is_success() {
        return Err(format!("download answered HTTP {}", reply.status().as_u16()));
    }
    let body = reply.bytes().await.map_err(|e| format!("download failed ({e})"))?;
    if body.len() as u64 > DOWNLOAD_LIMIT || hex::encode(sha2::Sha256::digest(&body)) != sha256 {
        return Err("the download does not match its pinned checksum".into());
    }
    std::fs::write(dest, &body).map_err(|e| e.to_string())
}

/// `node` on PATH, when it is new enough for chrome-devtools-mcp (20.19 or 22.12 and later).
pub async fn node() -> Result<PathBuf, String> {
    let Some(node) = crate::env::which("node", &std::env::var("PATH").unwrap_or_default()) else {
        return Err("node is not installed on this computer".into());
    };
    let out = tokio::time::timeout(Duration::from_secs(20), tokio::process::Command::new(&node).arg("--version").output())
        .await
        .map_err(|_| "node --version did not answer".to_string())?
        .map_err(|e| format!("node did not run ({e})"))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let v = crate::harness_tools::version_key(&text);
    let (major, minor) = (v.first().copied().unwrap_or(0), v.get(1).copied().unwrap_or(0));
    let ok = major > 22 || (major == 22 && minor >= 12) || (major == 21) || (major == 20 && minor >= 19);
    if !ok {
        return Err(format!("node {text} is too old for chrome-devtools-mcp (20.19 or later)"));
    }
    Ok(node)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_index_fallback_takes_only_a_well_formed_pool_entry() {
        let index = "Package: google-chrome-beta\nVersion: 1\nFilename: pool/b.deb\nSize: 1\nSHA256: aa\n\n\
            Package: google-chrome-stable\nVersion: 157.0.1-1\nFilename: pool/main/g/google-chrome-stable/x_157.0.1-1_amd64.deb\nSize: 42\nSHA256: ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef0123456789\n";
        let (version, path, size, sha) = stanza(index).unwrap();
        assert_eq!((version.as_str(), size), ("157.0.1-1", 42));
        assert!(path.starts_with("pool/main/"));
        assert_eq!(sha, "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789");
        let escape = index.replace("pool/main/g/google-chrome-stable/x_157.0.1-1_amd64.deb", "pool/../../etc/x.deb");
        assert!(stanza(&escape).is_none());
        let short = index.replace("ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef0123456789", "abc");
        assert!(stanza(&short).is_none());
    }
}
