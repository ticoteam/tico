//! Company skills for a Claude turn (backend/skill_runtime.py, spec reports/2026-11-05-company-skills §4 Bots).
//!
//! At a turn's start the runner asks the server for the bot's installed skills (`GET attempts/{aid}/skills`), fetches
//! each bundle once into an immutable cache keyed by its sha (`<state>/skill-bundles/<sha>.json`, checked against
//! the sha on every read), stages them in a fresh folder of the turn's own (`<state>/skill-sessions/<aid>`, laid out
//! as `.claude/skills/<name>/...`) and adds that folder to Claude's `--add-dir` list: Claude Code loads the skills in
//! an added directory's `.claude/skills`. It then reports what it staged (`POST attempts/{aid}/skills/receipt`) and
//! remembers the staged skills for the bot's `/` menu (`menu`).
//!
//! Nothing here may fail a turn: any failure is logged and the turn runs without company skills (a skill that fails
//! alone is left out). Staged files are readable by everyone on the computer, so a turn run as its own leased user
//! can read them; they are the company's approved skills, which every bot may read anyway, and nobody but the runner
//! can write them.

use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use regex::Regex;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::client::Client;
use crate::util::log;

/// The bundle limits the server enforces (the shared contract); a bundle past them is refused, never truncated.
pub const MAX_FILES: usize = 100;
pub const MAX_CHARS: usize = 90_000;
pub const MAX_PATH_CHARS: usize = 200;
/// At most this many skills are staged for one turn.
pub const MAX_SKILLS: usize = 100;
const SESSIONS: &str = "skill-sessions";
const BUNDLES: &str = "skill-bundles";
const MENUS: &str = "skill-menus";
/// A turn's staged folder outlives any run; older ones are removed when a later turn stages.
const KEEP_SESSION: Duration = Duration::from_secs(2 * 24 * 3600);
/// The whole of staging, fetches included: past it the turn starts without company skills.
const DEADLINE: Duration = Duration::from_secs(30);

pub fn name_ok(name: &str) -> bool {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[a-z0-9][a-z0-9._-]{0,79}$").unwrap()).is_match(name)
}

pub fn sha_ok(sha: &str) -> bool {
    sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// An id that is safe as one file name: an attempt id or a bot id.
fn id_ok(id: &str) -> bool {
    !id.is_empty() && id.len() <= 200 && !id.starts_with('.') && id.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

#[derive(Debug, Clone, PartialEq)]
pub struct File {
    pub path: String,
    pub content: String,
}

/// The canonical bundle JSON (backend/skill_runtime.py `bundle_sha`): files sorted by path, each `{"content","path"}`,
/// compact, raw UTF-8. Written by hand because this crate's serde_json keeps insertion order.
pub fn canonical(files: &[File]) -> String {
    let mut sorted: Vec<&File> = files.iter().collect();
    sorted.sort_by(|a, b| a.path.cmp(&b.path));
    let items: Vec<String> = sorted.iter().map(|f| {
        format!("{{\"content\":{},\"path\":{}}}", serde_json::to_string(&f.content).unwrap(), serde_json::to_string(&f.path).unwrap())
    }).collect();
    format!("[{}]", items.join(","))
}

pub fn bundle_sha(files: &[File]) -> String {
    hex::encode(Sha256::digest(canonical(files).as_bytes()))
}

/// A bundle path the contract allows: relative, `/`-separated, no empty, `.` or `..` segment, no backslash or NUL.
pub fn path_ok(path: &str) -> bool {
    !path.is_empty() && path.chars().count() <= MAX_PATH_CHARS && !path.starts_with('/') && !path.contains('\\')
        && !path.contains('\0') && path.split('/').all(|s| !s.is_empty() && s != "." && s != "..")
}

/// A bundle's files, refused whole when any file breaks the contract's rules.
pub fn parse_files(value: &Value) -> Result<Vec<File>, String> {
    let items = value.as_array().ok_or("the bundle has no file list")?;
    if items.len() > MAX_FILES {
        return Err(format!("the bundle has more than {MAX_FILES} files"));
    }
    let mut files = Vec::with_capacity(items.len());
    let mut total = 0usize;
    let mut seen = std::collections::HashSet::new();
    for item in items {
        let (Some(path), Some(content)) = (item["path"].as_str(), item["content"].as_str()) else {
            return Err("a bundle file has no path or content".into());
        };
        if !path_ok(path) || !seen.insert(path.to_string()) {
            return Err("a bundle file has an unsafe or repeated path".into());
        }
        if content.contains('\0') {
            return Err("a bundle file is not text".into());
        }
        total += content.chars().count();
        if total > MAX_CHARS {
            return Err(format!("the bundle is over {MAX_CHARS} characters"));
        }
        files.push(File { path: path.into(), content: content.into() });
    }
    if !files.iter().any(|f| f.path == "SKILL.md") {
        return Err("the bundle has no SKILL.md".into());
    }
    Ok(files)
}

/// The bundle's files when they hash to `sha`, else why not.
pub fn verified(value: &Value, sha: &str) -> Result<Vec<File>, String> {
    let files = parse_files(value)?;
    if bundle_sha(&files) != sha {
        return Err("the bundle does not match its sha".into());
    }
    Ok(files)
}

fn make_dir(path: &Path) -> std::io::Result<()> {
    std::fs::DirBuilder::new().mode(0o755).create(path)?;
    // The mode a umask may have narrowed: a leased turn user reads these.
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))
}

/// Writes one skill's files under `<skills>/<name>`, which must not exist yet. Every folder on the way is made here
/// and checked to be a real folder, and every file is created new, so nothing is written through a link or outside it.
pub fn write_skill(skills: &Path, name: &str, files: &[File]) -> Result<(), String> {
    if !name_ok(name) {
        return Err("unsafe skill name".into());
    }
    let root = skills.join(name);
    make_dir(&root).map_err(|e| format!("cannot make its folder: {e}"))?;
    for file in files {
        if !path_ok(&file.path) {
            return Err("unsafe path".into());
        }
        let parts: Vec<&str> = file.path.split('/').collect();
        let mut dir = root.clone();
        for part in &parts[..parts.len() - 1] {
            dir.push(part);
            match std::fs::symlink_metadata(&dir) {
                Ok(m) if m.is_dir() => {}
                Ok(_) => return Err("a bundle path runs through a file".into()),
                Err(_) => make_dir(&dir).map_err(|e| format!("cannot make a folder: {e}"))?,
            }
        }
        let target = dir.join(parts[parts.len() - 1]);
        let mut out = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o644).open(&target)
            .map_err(|e| format!("cannot write a file: {e}"))?;
        out.write_all(file.content.as_bytes()).map_err(|e| format!("cannot write a file: {e}"))?;
        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o644));
    }
    Ok(())
}

/// A bundle from the cache, checked against its sha; a cached copy that no longer matches is removed.
fn cached(state_dir: &Path, sha: &str) -> Option<Vec<File>> {
    let path = state_dir.join(BUNDLES).join(format!("{sha}.json"));
    let value: Value = serde_json::from_slice(&std::fs::read(&path).ok()?).ok()?;
    match verified(&value, sha) {
        Ok(files) => Some(files),
        Err(_) => {
            let _ = std::fs::remove_file(&path);
            None
        }
    }
}

fn keep(state_dir: &Path, sha: &str, files: &[File]) -> std::io::Result<()> {
    let dir = state_dir.join(BUNDLES);
    std::fs::create_dir_all(&dir)?;
    let value: Vec<Value> = files.iter().map(|f| json!({"path": f.path, "content": f.content})).collect();
    let temp = dir.join(format!(".{sha}.{}", uuid::Uuid::new_v4().simple()));
    std::fs::write(&temp, serde_json::to_vec(&value)?)?;
    std::fs::rename(&temp, dir.join(format!("{sha}.json")))
}

/// Removes staged folders older than KEEP_SESSION.
fn prune(sessions: &Path) {
    let Ok(entries) = std::fs::read_dir(sessions) else { return };
    for entry in entries.flatten() {
        let Ok(meta) = std::fs::symlink_metadata(entry.path()) else { continue };
        let old = meta.modified().ok().and_then(|m| m.elapsed().ok()).is_some_and(|age| age > KEEP_SESSION);
        if old {
            let _ = if meta.is_dir() { std::fs::remove_dir_all(entry.path()) } else { std::fs::remove_file(entry.path()) };
        }
    }
}

/// A fresh `<state>/skill-sessions/<aid>` with `.claude/skills/<name>/...` for each bundle that writes whole; the
/// folder and the (name, sha) of what it holds. None when nothing could be staged.
pub fn stage(state_dir: &Path, aid: &str, bundles: &[(String, String, Vec<File>)]) -> Option<(PathBuf, Vec<(String, String)>)> {
    if !id_ok(aid) || bundles.is_empty() {
        return None;
    }
    let sessions = state_dir.join(SESSIONS);
    if let Err(e) = std::fs::create_dir_all(&sessions) {
        log(&format!("Tico runner: company skills: cannot make {}: {e}", sessions.display()));
        return None;
    }
    // Others may pass through to a turn's folder by name but not list them; the runner's restart narrows it again.
    let _ = std::fs::set_permissions(&sessions, std::fs::Permissions::from_mode(0o711));
    prune(&sessions);
    let dir = sessions.join(aid);
    if std::fs::symlink_metadata(&dir).is_ok() {
        let _ = std::fs::remove_dir_all(&dir).or_else(|_| std::fs::remove_file(&dir)); // a retried start stages again
    }
    let skills = dir.join(".claude").join("skills");
    for d in [&dir, &dir.join(".claude"), &skills] {
        if let Err(e) = make_dir(d) {
            log(&format!("Tico runner: company skills: cannot make the turn's folder: {e}"));
            return None;
        }
    }
    let mut staged = vec![];
    for (name, sha, files) in bundles {
        match write_skill(&skills, name, files) {
            Ok(()) => staged.push((name.clone(), sha.clone())),
            Err(e) => {
                log(&format!("Tico runner: company skills: left out {name}: {e}"));
                let _ = std::fs::remove_dir_all(skills.join(name));
            }
        }
    }
    if staged.is_empty() {
        let _ = std::fs::remove_dir_all(&dir);
        return None;
    }
    Some((dir, staged))
}

/// The turn's directories with the staged skills folder added, when there is one.
pub fn with_dir(mut add_dirs: Vec<String>, staged: Option<&Path>) -> Vec<String> {
    if let Some(dir) = staged {
        let dir = dir.display().to_string();
        if !add_dirs.contains(&dir) {
            add_dirs.push(dir);
        }
    }
    add_dirs
}

/// The staged skills a bot's `/` menu offers (readiness::document), from its last staged turn.
pub fn menu(state_dir: &Path, bot: &str) -> Vec<Value> {
    if !id_ok(bot) {
        return vec![];
    }
    std::fs::read(state_dir.join(MENUS).join(format!("{bot}.json"))).ok()
        .and_then(|b| serde_json::from_slice::<Vec<Value>>(&b).ok())
        .unwrap_or_default()
}

fn remember_menu(state_dir: &Path, bot: &str, staged: Option<&Path>) {
    if !id_ok(bot) {
        return;
    }
    let dir = state_dir.join(MENUS);
    let path = dir.join(format!("{bot}.json"));
    let commands = staged.map(crate::goals::repo_commands).unwrap_or_default();
    if commands.is_empty() {
        let _ = std::fs::remove_file(&path);
        return;
    }
    let temp = dir.join(format!(".{bot}.{}", uuid::Uuid::new_v4().simple()));
    let written = std::fs::create_dir_all(&dir)
        .and_then(|_| std::fs::write(&temp, Value::Array(commands).to_string()))
        .and_then(|_| std::fs::rename(&temp, &path));
    if let Err(e) = written {
        let _ = std::fs::remove_file(&temp);
        log(&format!("Tico runner: company skills: cannot keep {bot}'s menu: {e}"));
    }
}

/// The installed skills the server names for the attempt: valid (name, sha) pairs, each name once.
fn wanted(listing: &Value) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = vec![];
    for item in listing["skills"].as_array().into_iter().flatten() {
        let (name, sha) = (item["name"].as_str().unwrap_or_default(), item["sha256"].as_str().unwrap_or_default());
        if name_ok(name) && sha_ok(sha) && !out.iter().any(|(n, _)| n == name) && out.len() < MAX_SKILLS {
            out.push((name.into(), sha.into()));
        }
    }
    out
}

/// Stages the bot's company skills for this attempt and returns the folder to add, or None to run without them.
pub async fn prepare(client: &Client, state_dir: &Path, aid: &str, bot: &str) -> Option<PathBuf> {
    match tokio::time::timeout(DEADLINE, prepare_within(client, state_dir, aid, bot)).await {
        Ok(Ok(dir)) => dir,
        Ok(Err(e)) => {
            log(&format!("Tico runner: {bot}: running without company skills: {e}"));
            None
        }
        Err(_) => {
            log(&format!("Tico runner: {bot}: running without company skills: staging took over {}s", DEADLINE.as_secs()));
            None
        }
    }
}

async fn prepare_within(client: &Client, state_dir: &Path, aid: &str, bot: &str) -> Result<Option<PathBuf>, String> {
    if !id_ok(aid) {
        return Ok(None);
    }
    let listing = match client.get(&format!("attempts/{aid}/skills")).await {
        Ok(v) => v,
        Err(e) if e.status == 404 => return Ok(None), // a server without company skills
        Err(e) => return Err(format!("cannot read its installed skills: {e}")),
    };
    let want = wanted(&listing);
    let mut bundles = vec![];
    for (name, sha) in want {
        let files = match cached(state_dir, &sha) {
            Some(files) => files,
            None => {
                let fetched = client.get(&format!("skills/bundles/{sha}")).await
                    .map_err(|e| e.to_string())
                    .and_then(|v| verified(&v["files"], &sha));
                match fetched {
                    Ok(files) => {
                        if let Err(e) = keep(state_dir, &sha, &files) {
                            log(&format!("Tico runner: company skills: cannot cache {name}: {e}"));
                        }
                        files
                    }
                    Err(e) => {
                        log(&format!("Tico runner: {bot}: left out company skill {name}: {e}"));
                        continue;
                    }
                }
            }
        };
        bundles.push((name, sha, files));
    }
    let staged = stage(state_dir, aid, &bundles);
    remember_menu(state_dir, bot, staged.as_ref().map(|(dir, _)| dir.as_path()));
    let Some((dir, names)) = staged else { return Ok(None) };
    let body = json!({"staged": names.iter().map(|(n, s)| json!({"name": n, "sha256": s})).collect::<Vec<_>>()});
    let key = format!("skills-receipt:{aid}:{}", &hex::encode(Sha256::digest(body.to_string().as_bytes()))[..16]);
    if let Err(e) = client.post(&format!("attempts/{aid}/skills/receipt"), &body, Some(&key)).await {
        log(&format!("Tico runner: {bot}: could not report its staged company skills: {e}"));
    }
    Ok(Some(dir))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("tico-company-skills-{name}-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn brief() -> Vec<File> {
        vec![
            File { path: "SKILL.md".into(), content: "---\nname: acme-brief\ndescription: Write the Acme brief\n---\nWrite it.\n".into() },
            File { path: "notes/style.md".into(), content: "Plain words.\n".into() },
        ]
    }

    fn as_json(files: &[File]) -> Value {
        Value::Array(files.iter().map(|f| json!({"path": f.path, "content": f.content})).collect())
    }

    #[test]
    fn the_sha_matches_the_servers_canonical_json() {
        // The same vector as backend/tests/test_skill_runtime.py.
        let files = vec![File { path: "b.md".into(), content: "é\n\"q\"\t\u{1}".into() }, File { path: "SKILL.md".into(), content: "x".into() }];
        let text = "[{\"content\":\"x\",\"path\":\"SKILL.md\"},{\"content\":\"é\\n\\\"q\\\"\\t\\u0001\",\"path\":\"b.md\"}]";
        assert_eq!(canonical(&files), text);
        assert_eq!(bundle_sha(&files), hex::encode(Sha256::digest(text.as_bytes())));
    }

    #[test]
    fn a_bundle_that_does_not_match_its_sha_is_refused() {
        let files = brief();
        let sha = bundle_sha(&files);
        assert_eq!(verified(&as_json(&files), &sha).unwrap(), files);
        let mut changed = files.clone();
        changed[1].content.push('!');
        assert!(verified(&as_json(&changed), &sha).is_err());
    }

    #[test]
    fn unsafe_paths_and_bundles_are_refused() {
        for bad in ["/etc/passwd", "../x", "a/../../x", "a//b", "./a", "a\\b", "a\0b", ""] {
            assert!(!path_ok(bad), "{bad:?}");
        }
        assert!(path_ok("notes/style.md") && path_ok("SKILL.md"));
        let with = |path: &str| json!([{"path": "SKILL.md", "content": "x"}, {"path": path, "content": "y"}]);
        assert!(parse_files(&with("../escape.md")).is_err());
        assert!(parse_files(&with("SKILL.md")).is_err(), "a repeated path");
        assert!(parse_files(&json!([{"path": "other.md", "content": "x"}])).is_err(), "no SKILL.md");
        assert!(parse_files(&json!([{"path": "SKILL.md", "content": "a\0b"}])).is_err(), "binary");
        assert!(parse_files(&json!([{"path": "SKILL.md", "content": "x".repeat(MAX_CHARS + 1)}])).is_err(), "oversize");
        // Written anyway, a file path never leaves the skill's folder, and a name is checked too.
        let dir = temp("unsafe");
        assert!(write_skill(&dir, "acme", &[File { path: "../out.md".into(), content: "x".into() }]).is_err());
        assert!(!dir.join("out.md").exists());
        assert!(write_skill(&dir, "../acme", &brief()).is_err());
        // A file and a folder of the same name: refused, nothing written through it.
        let clash = [File { path: "a".into(), content: "x".into() }, File { path: "a/b".into(), content: "y".into() }];
        assert!(write_skill(&dir, "clash", &clash).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_link_in_the_way_is_never_written_through() {
        let dir = temp("link");
        let outside = temp("outside");
        std::fs::create_dir_all(dir.join("acme")).unwrap();
        std::os::unix::fs::symlink(&outside, dir.join("acme").join("notes")).unwrap();
        // A skill folder that is already there (here, with a link inside) is refused before anything is written.
        assert!(write_skill(&dir, "acme", &brief()).is_err());
        assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 0);
        std::fs::remove_dir_all(dir).unwrap();
        std::fs::remove_dir_all(outside).unwrap();
    }

    #[test]
    fn staging_lays_out_claude_skills_in_a_fresh_folder_per_attempt() {
        let state = temp("stage");
        let files = brief();
        let sha = bundle_sha(&files);
        let bad = vec![File { path: "SKILL.md".into(), content: "x".into() }, File { path: "SKILL.md/x".into(), content: "y".into() }];
        let (dir, staged) = stage(&state, "a-1", &[("acme-brief".into(), sha.clone(), files.clone()), ("acme-bad".into(), "0".repeat(64), bad)]).unwrap();
        assert_eq!(dir, state.join("skill-sessions").join("a-1"));
        assert_eq!(staged, vec![("acme-brief".to_string(), sha.clone())], "a skill that fails alone is left out");
        let root = dir.join(".claude").join("skills");
        assert_eq!(std::fs::read_to_string(root.join("acme-brief").join("SKILL.md")).unwrap(), files[0].content);
        assert_eq!(std::fs::read_to_string(root.join("acme-brief").join("notes").join("style.md")).unwrap(), files[1].content);
        assert!(!root.join("acme-bad").exists());
        let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(&root.join("acme-brief").join("SKILL.md")), 0o644);
        assert_eq!(mode(&root.join("acme-brief")), 0o755);
        // A retried start stages again from nothing: a file left from before is gone.
        std::fs::write(root.join("acme-brief").join("stale.md"), "old").unwrap();
        let (again, _) = stage(&state, "a-1", &[("acme-brief".into(), sha.clone(), files.clone())]).unwrap();
        assert!(!again.join(".claude/skills/acme-brief/stale.md").exists());
        // The bot's menu reads the staged folder as Claude does.
        remember_menu(&state, "ops", Some(&again));
        let offered = menu(&state, "ops");
        assert_eq!(offered.len(), 1);
        assert_eq!(offered[0]["name"], "acme-brief");
        assert_eq!(with_dir(vec!["/work/other".into()], Some(&again)), vec!["/work/other".to_string(), again.display().to_string()]);
        remember_menu(&state, "ops", None);
        assert!(menu(&state, "ops").is_empty());
        assert!(stage(&state, "../a", &[("acme-brief".into(), sha, files)]).is_none());
        std::fs::remove_dir_all(state).unwrap();
    }

    #[tokio::test]
    async fn prepare_fetches_verifies_caches_stages_and_reports() {
        let state = temp("prepare");
        let files = brief();
        let sha = bundle_sha(&files);
        let listing: &'static str = Box::leak(json!({"skills": [{"name": "acme-brief", "version": 2, "sha256": sha, "description": ""},
                                                               {"name": "../bad", "version": 1, "sha256": sha}]}).to_string().into_boxed_str());
        let bundle: &'static str = Box::leak(json!({"name": "acme-brief", "version": 2, "sha256": sha, "files": as_json(&files)}).to_string().into_boxed_str());
        let (url, seen) = crate::client::tests::serve(vec![(200, listing), (200, bundle), (200, "{\"kept\":1}"), (200, listing), (200, "{\"kept\":1}")]).await;
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        let dir = prepare(&client, &state, "a-7", "ops").await.unwrap();
        assert!(dir.join(".claude/skills/acme-brief/SKILL.md").is_file());
        assert!(state.join("skill-bundles").join(format!("{sha}.json")).is_file());
        // The next turn reads the bundle from the cache: no second bundle fetch.
        let again = prepare(&client, &state, "a-8", "ops").await.unwrap();
        assert!(again.join(".claude/skills/acme-brief/SKILL.md").is_file());
        let seen = seen.lock().unwrap();
        let heads: Vec<&str> = seen.iter().map(|r| r.lines().next().unwrap()).collect();
        let bundle_head = format!("GET /api/v2/skills/bundles/{sha} HTTP/1.1");
        assert_eq!(heads, vec!["GET /api/v2/attempts/a-7/skills HTTP/1.1", bundle_head.as_str(),
                               "POST /api/v2/attempts/a-7/skills/receipt HTTP/1.1", "GET /api/v2/attempts/a-8/skills HTTP/1.1",
                               "POST /api/v2/attempts/a-8/skills/receipt HTTP/1.1"]);
        assert!(seen[2].ends_with(&format!("{{\"staged\":[{{\"name\":\"acme-brief\",\"sha256\":\"{sha}\"}}]}}")), "{}", seen[2]);
        drop(seen);
        std::fs::remove_dir_all(state).unwrap();
    }

    #[tokio::test]
    async fn any_failure_runs_the_turn_without_company_skills() {
        let state = temp("fail");
        let files = brief();
        let sha = bundle_sha(&files);
        let listing: &'static str = Box::leak(json!({"skills": [{"name": "acme-brief", "version": 1, "sha256": sha}]}).to_string().into_boxed_str());
        // A bundle whose files are not what its sha says.
        let mut changed = files.clone();
        changed[0].content = "tampered".into();
        let tampered: &'static str = Box::leak(json!({"files": as_json(&changed)}).to_string().into_boxed_str());
        let (url, seen) = crate::client::tests::serve(vec![
            (404, "{\"detail\":\"Not Found\"}"),                                    // a server without company skills
            (500, "{\"error\":{\"code\":\"boom\",\"detail\":\"Synthetic\",\"retryable\":false}}"),
            (200, listing), (200, tampered),
            (200, "{}"),                                                             // nothing listed
        ]).await;
        let client = Client::new(&url, "runner-token", 5, 0).unwrap();
        for aid in ["a-1", "a-2", "a-3", "a-4"] {
            let staged = prepare(&client, &state, aid, "ops").await;
            assert!(staged.is_none(), "{aid}");
            assert_eq!(with_dir(vec![], staged.as_deref()), Vec::<String>::new());
        }
        assert!(!state.join("skill-bundles").join(format!("{sha}.json")).exists(), "a bad bundle is never cached");
        assert!(menu(&state, "ops").is_empty());
        assert_eq!(seen.lock().unwrap().len(), 5, "no receipt for nothing staged");
        std::fs::remove_dir_all(state).unwrap();
    }
}
