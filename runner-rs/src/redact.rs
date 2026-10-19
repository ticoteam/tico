//! Keep the secrets a turn was granted out of what the runner writes down (`runner/redact.py`).
//!
//! One redactor per turn holds its granted values and the common ways each turns up (URL-encoded, base64
//! with and without padding, the password half of `user:password`) and replaces them with `••••`, longest
//! match first, in one pass. A turn with nothing worth hiding has no redactor.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
#[cfg(test)]
use std::process::Command;
use std::sync::{Arc, Mutex, OnceLock, RwLock};

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
use base64::Engine;
use serde_json::Value;

pub const MASK: &str = "••••";
pub const MIN_LENGTH: usize = 8;
const COMMON: &[&str] = &[
    "localhost", "127.0.0.1", "production", "development", "password", "username", "enabled", "disabled", "undefined",
    "identity", "anonymous", "internal", "external", "default", "standard", "true", "false",
];
const TEXT_LIMIT: u64 = 2_000_000;

/// `urllib.parse.quote(value, safe="")`.
pub fn quote(value: &str) -> String {
    let mut out = String::new();
    for b in value.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn variants(value: &str) -> BTreeSet<String> {
    let mut forms = BTreeSet::new();
    forms.insert(value.to_string());
    forms.insert(quote(value));
    forms.insert(crate::client::quote_plus(value));
    for text in [
        base64::engine::general_purpose::STANDARD.encode(value.as_bytes()),
        base64::engine::general_purpose::URL_SAFE.encode(value.as_bytes()),
    ] {
        forms.insert(text.trim_end_matches('=').to_string());
        forms.insert(text);
    }
    forms
}

fn worth(value: &str) -> bool {
    if value.chars().count() < MIN_LENGTH || COMMON.contains(&value.to_lowercase().as_str()) {
        return false;
    }
    value.chars().collect::<BTreeSet<_>>().len() >= 3
}

fn values(secrets: &[String]) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    for value in secrets {
        let value = value.trim();
        if !worth(value) {
            continue;
        }
        found.insert(value.to_string());
        if let Some((_, tail)) = value.split_once(':') {
            if !value.contains('\n') && worth(tail) {
                found.insert(tail.to_string());
            }
        }
    }
    found
}

struct Built {
    values: Vec<String>,
    matcher: Option<AhoCorasick>,
}

fn build(secrets: &[String]) -> Built {
    let mut vals: Vec<String> = values(secrets).into_iter().collect();
    vals.sort_by_key(|b| std::cmp::Reverse(b.len()));
    let mut forms = BTreeSet::new();
    for v in &vals {
        forms.extend(variants(v));
    }
    forms.remove("");
    let matcher = if forms.is_empty() {
        None
    } else {
        AhoCorasickBuilder::new().match_kind(MatchKind::LeftmostLongest).build(forms.iter()).ok()
    };
    Built { values: vals, matcher }
}

pub struct Redactor {
    inner: RwLock<Built>,
}

impl Redactor {
    pub fn new(secrets: &[String]) -> Redactor {
        Redactor { inner: RwLock::new(build(secrets)) }
    }

    /// Holds the redactor for `d`, the way a scrub stuck in git or in a large file holds its thread; `held` hears
    /// once it is.
    #[cfg(test)]
    pub fn hold_for(&self, d: std::time::Duration, held: std::sync::mpsc::Sender<()>) {
        let _held = self.inner.write().unwrap();
        held.send(()).unwrap();
        std::thread::sleep(d);
    }

    pub fn is_empty(&self) -> bool {
        self.inner.read().unwrap().matcher.is_none()
    }

    #[allow(dead_code)]
    pub fn values(&self) -> Vec<String> {
        self.inner.read().unwrap().values.clone()
    }

    pub fn add(&self, secrets: &[String]) {
        let mut inner = self.inner.write().unwrap();
        let fresh: Vec<String> = values(secrets).into_iter().filter(|v| !inner.values.contains(v)).collect();
        if fresh.is_empty() {
            return;
        }
        let mut all = inner.values.clone();
        all.extend(fresh);
        *inner = build(&all);
    }

    pub fn scrub_text(&self, text: &str) -> String {
        match &self.inner.read().unwrap().matcher {
            Some(m) => m.replace_all(text, &vec![MASK; m.patterns_len()]),
            None => text.to_string(),
        }
    }

    /// Bytes scrubbed as they are: bytes that are not UTF-8 are kept, not replaced (Python's surrogateescape).
    pub fn scrub_bytes(&self, data: &[u8]) -> Vec<u8> {
        match &self.inner.read().unwrap().matcher {
            Some(m) => m.replace_all_bytes(data, &vec![MASK.as_bytes(); m.patterns_len()]),
            None => data.to_vec(),
        }
    }

    /// The same structure with every string scrubbed; keys are left as they are.
    pub fn scrub_json(&self, v: &Value) -> Value {
        match v {
            Value::String(s) => Value::String(self.scrub_text(s)),
            Value::Array(items) => Value::Array(items.iter().map(|i| self.scrub_json(i)).collect()),
            Value::Object(map) => Value::Object(map.iter().map(|(k, i)| (k.clone(), self.scrub_json(i))).collect()),
            other => other.clone(),
        }
    }

    /// Whether these bytes carry a secret in any of its forms (read as latin-1, like the Python check).
    pub fn holds(&self, data: &[u8]) -> bool {
        match &self.inner.read().unwrap().matcher {
            Some(m) => {
                let text: String = data.iter().map(|&b| b as char).collect();
                // Latin-1 widening keeps ASCII as is; the forms are UTF-8 strings, so search both.
                m.is_match(&text) || m.is_match(String::from_utf8_lossy(data).as_ref())
            }
            None => false,
        }
    }

    /// Scrub what a turn changed in the checkout at `root`: rewrite changed text files holding a secret,
    /// report binary or oversized ones, and whether a commit made since `since` carries one.
    pub fn scrub_tree(&self, root: &Path, since: &str) -> Tree {
        let mut result = Tree::default();
        if self.is_empty() || !root.join(".git").exists() {
            return result;
        }
        let changed = git(root, &["status", "--porcelain", "-z", "--untracked-files=all"]).unwrap_or_default();
        for name in changed_paths(&changed) {
            let path = root.join(&name);
            match std::fs::symlink_metadata(&path) {
                Ok(m) if m.is_file() && m.len() > TEXT_LIMIT => result.too_large.push(path),
                Ok(m) if m.is_file() => match self.scrub_file(root, &name) {
                    Scrubbed::Clean => {}
                    Scrubbed::Rewritten => result.rewritten.push(path),
                    Scrubbed::LeftOut => result.left_out.push(path),
                },
                _ => {}
            }
        }
        if !since.is_empty() {
            let range = format!("{since}..HEAD");
            if let Some(patch) = git(root, &["log", "-p", "--no-ext-diff", "--no-textconv", "--format=", &range]) {
                result.committed = !patch.is_empty() && self.holds(&patch);
            }
        }
        result
    }
}

enum Scrubbed {
    Clean,
    Rewritten,
    LeftOut,
}

/// The paths `git status --porcelain -z` lists as changed and still present, relative to the checkout. Each
/// record is `XY path`; a rename or copy (`R`/`C` in either column) is followed by its source path as a field of
/// its own, which is skipped. Paths stay bytes: a name need not be UTF-8.
pub fn changed_paths(out: &[u8]) -> Vec<PathBuf> {
    use std::os::unix::ffi::OsStrExt;
    let mut paths = vec![];
    let mut fields = out.split(|&b| b == 0);
    while let Some(record) = fields.next() {
        let (Some(&x), Some(&y), Some(&b' ')) = (record.first(), record.get(1), record.get(2)) else { continue };
        if [x, y].iter().any(|c| *c == b'R' || *c == b'C') {
            fields.next(); // the source path
        }
        let name = &record[3..];
        if name.is_empty() || (x, y) == (b' ', b'D') || (x, y) == (b'D', b' ') {
            continue;
        }
        paths.push(PathBuf::from(std::ffi::OsStr::from_bytes(name)));
    }
    paths
}

/// An open directory: closed on drop.
struct Dir(std::os::fd::OwnedFd);

impl Dir {
    fn open_at(parent: Option<&Dir>, name: &std::ffi::CStr) -> std::io::Result<Dir> {
        use std::os::fd::{AsRawFd, FromRawFd};
        let at = parent.map_or(libc::AT_FDCWD, |d| d.0.as_raw_fd());
        let fd = unsafe { libc::openat(at, name.as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(Dir(unsafe { std::os::fd::OwnedFd::from_raw_fd(fd) }))
    }

    fn raw(&self) -> i32 {
        use std::os::fd::AsRawFd;
        self.0.as_raw_fd()
    }
}

fn cstr(bytes: &[u8]) -> std::io::Result<std::ffi::CString> {
    std::ffi::CString::new(bytes).map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))
}

fn stat_at(dir: &Dir, name: &std::ffi::CStr) -> std::io::Result<libc::stat> {
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstatat(dir.raw(), name.as_ptr(), &mut st, libc::AT_SYMLINK_NOFOLLOW) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(st)
}

impl Redactor {
    /// Rewrite one changed file (redact.py scrub_files) with every directory on its way opened by descriptor and
    /// without following links, so a directory swapped for a symlink after git listed the file cannot send the
    /// read or the replacement outside the checkout. The file is replaced only if it is still the one read.
    fn scrub_file(&self, root: &Path, name: &Path) -> Scrubbed {
        match self.scrub_anchored(root, name) {
            Ok(done) => done,
            Err(_) => Scrubbed::LeftOut,
        }
    }

    fn scrub_anchored(&self, root: &Path, name: &Path) -> std::io::Result<Scrubbed> {
        use std::io::{Read, Write};
        use std::os::fd::FromRawFd;
        use std::os::unix::ffi::OsStrExt;
        let anchored = root.canonicalize()?.join(name);
        let parts: Vec<&std::ffi::OsStr> = anchored.components().filter_map(|c| match c {
            std::path::Component::Normal(p) => Some(p),
            _ => None,
        }).collect();
        let Some((file, parents)) = parts.split_last() else { return Ok(Scrubbed::LeftOut) };
        if name.components().any(|c| !matches!(c, std::path::Component::Normal(_))) {
            return Ok(Scrubbed::LeftOut); // `..` or an absolute name is never followed out of the checkout
        }
        let mut dir = Dir::open_at(None, c"/")?;
        for part in parents {
            dir = Dir::open_at(Some(&dir), &cstr(part.as_bytes())?)?;
        }
        let leaf = cstr(file.as_bytes())?;
        let fd = unsafe { libc::openat(dir.raw(), leaf.as_ptr(), libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC) };
        if fd < 0 {
            let err = std::io::Error::last_os_error();
            // A symlink is not scrubbed through: what it points at is not the checkout's.
            if stat_at(&dir, &leaf).is_ok_and(|st| (st.st_mode & libc::S_IFMT) == libc::S_IFLNK) {
                return Ok(Scrubbed::Clean);
            }
            return Err(err);
        }
        let mut stream = unsafe { std::fs::File::from_raw_fd(fd) };
        let info = stream.metadata()?;
        if !info.is_file() {
            return Ok(Scrubbed::Clean);
        }
        if info.len() > TEXT_LIMIT {
            return Ok(Scrubbed::LeftOut);
        }
        let mut data = vec![];
        Read::by_ref(&mut stream).take(TEXT_LIMIT + 1).read_to_end(&mut data)?;
        drop(stream);
        if !self.holds(&data) {
            return Ok(Scrubbed::Clean);
        }
        if data.iter().take(8000).any(|&b| b == 0) || data.len() as u64 > TEXT_LIMIT {
            return Ok(Scrubbed::LeftOut);
        }
        use std::os::unix::fs::MetadataExt;
        let mode = (info.mode() & 0o7777) as libc::mode_t;
        let temporary = cstr(format!(".tico-scrub-{}", uuid::Uuid::new_v4().simple()).as_bytes())?;
        let fd = unsafe {
            libc::openat(dir.raw(), temporary.as_ptr(), libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC, mode as libc::c_uint)
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        let written = (|| {
            let mut out = unsafe { std::fs::File::from_raw_fd(fd) };
            if unsafe { libc::fchmod(fd, mode) } != 0 {
                return Err(std::io::Error::last_os_error());
            }
            out.write_all(&self.scrub_bytes(&data))?;
            out.sync_all()?;
            let current = stat_at(&dir, &leaf)?;
            if (current.st_dev as u64, current.st_ino as u64) != (info.dev(), info.ino()) {
                return Ok(Scrubbed::LeftOut); // replaced since it was read
            }
            if unsafe { libc::renameat(dir.raw(), temporary.as_ptr(), dir.raw(), leaf.as_ptr()) } != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(Scrubbed::Rewritten)
        })();
        if !matches!(written, Ok(Scrubbed::Rewritten)) {
            unsafe { libc::unlinkat(dir.raw(), temporary.as_ptr(), 0) };
        }
        written
    }
}

#[derive(Default, Debug)]
pub struct Tree {
    pub rewritten: Vec<PathBuf>,
    pub left_out: Vec<PathBuf>,
    pub too_large: Vec<PathBuf>,
    pub committed: bool,
}

/// Credentials that can turn up in git's or the hub's words whoever's they are: a URL's user part, GitHub's
/// token formats, Authorization headers and `name=value` credential pairs (redact.py _CREDENTIAL_SHAPES).
fn credential_shapes() -> &'static [(regex::Regex, &'static str)] {
    static SHAPES: OnceLock<Vec<(regex::Regex, &'static str)>> = OnceLock::new();
    SHAPES.get_or_init(|| {
        [
            (r#"(?i)([a-z][a-z0-9+.-]*://)[^/\s@'"]+@"#, "${1}••••@"),
            (r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})", "••••"),
            (r"(?i)\b((?:proxy-)?authorization\s*:\s*)(?:(?:basic|bearer|token)\s+)?\S+", "${1}••••"),
            (r"(?i)\b(basic|bearer)\s+[A-Za-z0-9+/._~-]{8,}=*", "${1} ••••"),
            (r"(?i)\b(password|oauth_refresh_token|access_token|token)\s*=\s*\S+", "${1}=••••"),
        ]
        .into_iter()
        .map(|(p, r)| (regex::Regex::new(p).expect("credential shape"), r))
        .collect()
    })
}

/// `text` with `secrets`, every running turn's secrets and anything shaped like a credential masked
/// (redact.py scrub_credentials). Always on the whole text: a token cut in half no longer looks like one.
pub fn scrub_credentials(text: &str, secrets: &[String]) -> String {
    let mut out = scrub_log(&Redactor::new(secrets).scrub_text(text));
    for (pattern, replacement) in credential_shapes() {
        out = pattern.replace_all(&out, *replacement).into_owned();
    }
    out
}

/// The secret values in a git environment: its tokens, by name and in TICO_GITHUB_TOKENS (redact.py env_secrets).
pub fn env_secrets(env: &std::collections::BTreeMap<String, String>) -> Vec<String> {
    let pairs: Vec<(String, String)> = env.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    for_turn(&pairs, &[]).map(|r| r.values()).unwrap_or_default()
}

/// Why a git command failed, in git's words and safe to show (redact.py git_failure): the last meaningful lines
/// of its stderr without progress or advice, scrubbed whole before anything is cut, at most `limit` characters.
pub fn git_failure(stderr: &str, secrets: &[String], limit: usize) -> String {
    static NOISE: OnceLock<regex::Regex> = OnceLock::new();
    static REASON: OnceLock<regex::Regex> = OnceLock::new();
    let noise = NOISE.get_or_init(|| {
        regex::Regex::new(
            r"(?i)^(?:hint:|cloning into|remote: (?:enumerating|counting|compressing|total)|receiving objects|resolving deltas|updating files|filtering content|checking out files|you can inspect what was checked out|and retry with|warning: clone succeeded|fatal: credential helper .* told us to quit)",
        )
        .unwrap()
    });
    let reason = REASON.get_or_init(|| {
        regex::Regex::new(r"(?i)^(?:fatal:|error:|remote:|tico\b|git-lfs|batch response|smudge|.*\b(?:error|denied|not found|timed out|too long|failed)\b)").unwrap()
    });
    let mut lines: Vec<String> = vec![];
    for raw in scrub_credentials(&stderr.replace('\r', "\n"), secrets).lines() {
        let line = raw.split_whitespace().collect::<Vec<_>>().join(" ");
        if !line.is_empty() && !noise.is_match(&line) && !lines.contains(&line) {
            lines.push(line);
        }
    }
    let mut picked: Vec<&String> = lines.iter().filter(|l| reason.is_match(l)).collect();
    if picked.is_empty() {
        picked = lines.iter().collect();
    }
    let start = picked.len().saturating_sub(3);
    let text = picked[start..].iter().map(|l| l.chars().take(200).collect::<String>()).collect::<Vec<_>>().join(" / ");
    if text.chars().count() <= limit {
        text
    } else {
        format!("{}…", text.chars().take(limit.saturating_sub(1)).collect::<String>().trim_end())
    }
}

/// git with the clean environment `runner/safe_git.py` uses: no global or system config.
pub fn git(root: &Path, args: &[&str]) -> Option<Vec<u8>> {
    let mut argv: Vec<String> = vec!["git".into()];
    argv.extend(crate::safegit::args(root));
    argv.extend(args.iter().map(|a| a.to_string()));
    let began = std::time::Instant::now();
    let ran = crate::isolation::std_command("git")
        .args(&argv[1..])
        .env_clear()
        .envs(crate::safegit::clean_environment(&crate::env::process_environment()))
        .stdin(std::process::Stdio::null())
        .output();
    // The scrub's git calls are in the flight file like gitrun's: a slow `status` or `log -p` names itself.
    crate::flight::git(&argv, began.elapsed().as_millis() as u64, ran.as_ref().ok().and_then(|o| o.status.code()), ran.as_ref().err().map(|_| "io"));
    let out = ran.ok()?;
    out.status.success().then_some(out.stdout)
}

/// The checkout's current commit, or "" when it has none.
pub fn head(root: &Path) -> String {
    git(root, &["rev-parse", "--verify", "-q", "HEAD"]).map(|o| String::from_utf8_lossy(&o).trim().to_string()).unwrap_or_default()
}

/// The turn's redactor, or None when it holds nothing worth hiding: the granted values, any variable whose
/// name says it is a secret, and the GitHub tokens handed to the turn.
pub fn for_turn(env: &[(String, String)], vault_values: &[String]) -> Option<Arc<Redactor>> {
    let mut secrets: Vec<String> = vault_values.to_vec();
    for (k, v) in env {
        let upper = k.to_uppercase();
        if ["TOKEN", "SECRET", "PASSWORD", "API_KEY"].iter().any(|s| upper.contains(s)) {
            secrets.push(v.clone());
        }
        if k == "TICO_GITHUB_TOKENS" {
            if let Ok(Value::Array(groups)) = serde_json::from_str::<Value>(v) {
                for g in groups {
                    if let Some(t) = g.get("token").and_then(|t| t.as_str()) {
                        secrets.push(t.to_string());
                    }
                }
            }
        }
    }
    let r = Redactor::new(&secrets);
    (!r.is_empty()).then(|| Arc::new(r))
}

#[allow(clippy::type_complexity)]
fn active() -> &'static Mutex<Vec<(String, Arc<Redactor>)>> {
    #[allow(clippy::type_complexity)]
    static ACTIVE: OnceLock<Mutex<Vec<(String, Arc<Redactor>)>>> = OnceLock::new();
    ACTIVE.get_or_init(|| Mutex::new(Vec::new()))
}

pub fn register(aid: &str, r: Arc<Redactor>) {
    active().lock().unwrap().push((aid.to_string(), r));
}

pub fn release(aid: &str) {
    active().lock().unwrap().retain(|(a, _)| a != aid);
}

/// A log line with every running turn's secrets taken out.
pub fn scrub_log(line: &str) -> String {
    let list: Vec<Arc<Redactor>> = active().lock().map(|l| l.iter().map(|(_, r)| r.clone()).collect()).unwrap_or_default();
    let mut out = line.to_string();
    for r in list {
        out = r.scrub_text(&out);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_secret_is_masked_in_every_form_and_short_or_common_values_are_not() {
        let r = Redactor::new(&["s3cr3t-value/x".into(), "short".into(), "password".into(), "user:hunter2hunter2".into()]);
        let b64 = base64::engine::general_purpose::STANDARD.encode("s3cr3t-value/x");
        let text = format!("a s3cr3t-value/x b {} c {} d hunter2hunter2 e short password", quote("s3cr3t-value/x"), b64);
        assert_eq!(r.scrub_text(&text), "a •••• b •••• c •••• d •••• e short password");
        assert!(Redactor::new(&["aaaaaaaaaaaa".into()]).is_empty(), "fewer than three distinct characters");
    }

    #[test]
    fn a_renamed_non_ascii_file_is_parsed_and_scrubbed_in_place() {
        // The source path of a rename is its own field; reading it as a record split a UTF-8 character.
        let out = "R  new.txt\0漢字.txt\0 M a b.txt\0?? 漢字2.txt\0 D gone.txt\0".as_bytes();
        let names: Vec<String> = changed_paths(out).iter().map(|p| p.display().to_string()).collect();
        assert_eq!(names, ["new.txt", "a b.txt", "漢字2.txt"]);
        assert!(changed_paths(b"R").is_empty() && changed_paths("漢".as_bytes()).is_empty());

        let root = std::env::temp_dir().join(format!("tico-scrub-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&root).unwrap();
        let sh = |cmd: &str| assert!(Command::new("sh").arg("-c").arg(cmd).current_dir(&root).env("GIT_CONFIG_GLOBAL", "/dev/null").output().unwrap().status.success());
        sh("git init -q && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init");
        let body = "a line that stays the same\n".repeat(20);
        std::fs::write(root.join("漢字.txt"), &body).unwrap();
        sh("git add . && git -c user.name=t -c user.email=t@t commit -q -m add && git mv 漢字.txt 新しい.txt");
        let mut text = body.into_bytes();
        text.extend(b"key=granted-secret-value \xff\n");
        std::fs::write(root.join("新しい.txt"), &text).unwrap();
        sh("git add -A");
        let status = git(&root, &["status", "--porcelain", "-z"]).unwrap();
        assert!(status.starts_with(b"R  "), "{}", String::from_utf8_lossy(&status));
        let tree = Redactor::new(&["granted-secret-value".into()]).scrub_tree(&root, "");
        assert_eq!(tree.rewritten.len(), 1, "{tree:?}");
        let written = std::fs::read(root.join("新しい.txt")).unwrap();
        assert!(written.ends_with(b"key=\xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2\xe2\x80\xa2 \xff\n"), "non-UTF-8 bytes kept, secret masked");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_python_redactor_agrees() {
        let secrets = ["granted-file-secret-fixture", "Zm9vYmFyYmF6cXV4", "a:b c+d/e=fghij"];
        let sample = "x granted-file-secret-fixture y Z3JhbnRlZC1maWxlLXNlY3JldC1maXh0dXJl z a%3Ab%20c%2Bd%2Fe%3Dfghij w b c+d/e=fghij";
        let r = Redactor::new(&secrets.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        let script = "import sys,json\nsys.path.insert(0,sys.argv[1])\nfrom runner.redact import Redactor\nprint(Redactor(json.loads(sys.argv[2])).scrub_text(sys.argv[3]))";
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let out = Command::new("python3")
            .args(["-c", script])
            .arg(repo)
            .arg(serde_json::to_string(&secrets).unwrap())
            .arg(sample)
            .output()
            .unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        assert_eq!(r.scrub_text(sample), String::from_utf8_lossy(&out.stdout).trim_end_matches('\n'));
    }
}
