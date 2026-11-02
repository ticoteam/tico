//! Read one immutable repository snapshot; write findings only through the shared Hub CLI.
use flate2::read::GzDecoder;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{File, OpenOptions},
    io::Read,
    path::Path,
    process::{self, Command},
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, String>;
const TITLE: &str = "Update bundled trust lists";
const MARKER: &str = "[tico-trust-list-currency:v1]";
const OWNER: &str = "bot:engineering-lead";

fn run(program: &str, args: &[&str], key: Option<&str>) -> Result<Vec<u8>> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    if let Some(key) = key {
        cmd.env("HUB_OPERATION_ID", key);
    }
    let out = cmd.output().map_err(|e| format!("{program}: {e}"))?;
    if !out.status.success() {
        // Preserve the complete refusal and operation id; never retry a write here.
        return Err(format!(
            "{program} failed: {}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    Ok(out.stdout)
}
fn text(bytes: Vec<u8>) -> Result<String> {
    String::from_utf8(bytes).map_err(|e| e.to_string())
}
fn git(repo: &str, args: &[&str]) -> Result<String> {
    text(run("git", &[&["-C", repo][..], args].concat(), None)?)
}
fn hub(args: &[&str], key: Option<&str>) -> Result<Value> {
    let value: Value =
        serde_json::from_slice(&run("hub", args, key)?).map_err(|e| e.to_string())?;
    if value.get("error").is_some() {
        return Err(value.to_string());
    }
    Ok(value)
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Missing {key} in response"))
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn download(url: &str) -> Result<Vec<u8>> {
    if !url.starts_with("https://") {
        return Err("Only HTTPS registry downloads are allowed".into());
    }
    run(
        "curl",
        &[
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-time",
            "45",
            "--max-filesize",
            "16777216",
            "--user-agent",
            "tico-trust-list-check/1",
            url,
        ],
        None,
    )
}
fn get_json(url: &str) -> Result<Value> {
    serde_json::from_slice(&download(url)?).map_err(|e| e.to_string())
}
fn member(archive: &[u8], name: &str) -> Result<String> {
    let mut tar = tar::Archive::new(GzDecoder::new(archive));
    for entry in tar.entries().map_err(|e| e.to_string())? {
        let mut entry = entry.map_err(|e| e.to_string())?;
        if entry.path().map_err(|e| e.to_string())?.as_ref() == Path::new(name) {
            let mut content = String::new();
            entry
                .by_ref()
                .take(16 * 1024 * 1024 + 1)
                .read_to_string(&mut content)
                .map_err(|e| e.to_string())?;
            if content.len() > 16 * 1024 * 1024 {
                return Err("Oversized archive member".into());
            }
            return Ok(content);
        }
    }
    Err(format!("Archive missing {name}"))
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
struct Pin {
    file: String,
    package: String,
    version: String,
}
fn pin(file: &str, package: &str, version: &str) -> Result<Pin> {
    if version.is_empty()
        || !version
            .split('.')
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err(format!(
            "{file}: {package} requires an exact stable version, got {version:?}"
        ));
    }
    Ok(Pin {
        file: file.into(),
        package: package.into(),
        version: version.into(),
    })
}
fn requirement(file: &str, value: &str) -> Result<Option<Pin>> {
    let s = value.trim();
    let lower = s.to_ascii_lowercase();
    let Some(mut rest) = lower.strip_prefix("certifi") else {
        return Ok(None);
    };
    if rest.starts_with(|c: char| c.is_alphanumeric() || c == '-' || c == '_') {
        return Ok(None);
    }
    rest = rest.trim_start();
    if rest.starts_with('[') {
        rest = rest
            .split_once(']')
            .ok_or("Malformed certifi extras")?
            .1
            .trim_start();
    }
    rest = rest
        .strip_prefix("===")
        .or_else(|| rest.strip_prefix("=="))
        .ok_or_else(|| format!("{file}: certifi must be exactly pinned"))?
        .trim_start();
    let version = rest
        .split(|c: char| c.is_whitespace() || c == ';' || c == '#' || c == '\\')
        .next()
        .unwrap_or("");
    Ok(Some(pin(file, "certifi", version)?))
}
fn python_dependencies(file: &str, value: &toml::Value, pins: &mut BTreeSet<Pin>) -> Result<()> {
    match value {
        toml::Value::String(s) => {
            if let Some(p) = requirement(file, s)? {
                pins.insert(p);
            }
        }
        toml::Value::Array(a) => {
            for v in a {
                python_dependencies(file, v, pins)?;
            }
        }
        toml::Value::Table(t) => {
            for (k, v) in t {
                if k.eq_ignore_ascii_case("certifi") {
                    let s = v
                        .as_str()
                        .or_else(|| v.get("version").and_then(toml::Value::as_str))
                        .ok_or_else(|| format!("{file}: unsupported certifi dependency"))?;
                    pins.insert(pin(file, "certifi", s.trim_start_matches('='))?);
                } else {
                    python_dependencies(file, v, pins)?;
                }
            }
        }
        _ => {}
    }
    Ok(())
}
fn python_toml(file: &str, value: &toml::Value, pins: &mut BTreeSet<Pin>) -> Result<()> {
    // Read dependency declarations, not project descriptions or tool source URLs.
    if let Some(table) = value.as_table() {
        for (key, value) in table {
            if key.ends_with("dependencies") || key == "dependency-groups" || key == "requires" {
                python_dependencies(file, value, pins)?;
            } else if value.is_table() {
                python_toml(file, value, pins)?;
            }
        }
    }
    Ok(())
}
fn inventory(files: &BTreeMap<String, String>) -> Result<Vec<Pin>> {
    let mut pins = BTreeSet::new();
    for (file, content) in files {
        if file.ends_with("Cargo.lock") {
            let lock: toml::Value = content
                .parse::<toml::Value>()
                .map_err(|e| format!("{file}: {e}"))?;
            let packages = lock
                .get("package")
                .and_then(toml::Value::as_array)
                .ok_or_else(|| format!("{file}: missing packages"))?;
            let roots: Vec<_> = packages
                .iter()
                .filter(|p| p.get("name").and_then(toml::Value::as_str) == Some("webpki-roots"))
                .collect();
            for p in &roots {
                let version = p
                    .get("version")
                    .and_then(toml::Value::as_str)
                    .ok_or("Missing lock version")?;
                // A shim has an explicit dependency on another locked webpki-roots package.
                // Do not blindly skip all 0.26 releases: early 0.26 releases carried their own roots.
                let delegates = p
                    .get("dependencies")
                    .and_then(toml::Value::as_array)
                    .is_some_and(|deps| {
                        deps.iter().any(|d| {
                            let words: Vec<_> =
                                d.as_str().unwrap_or("").split_whitespace().collect();
                            words.first() == Some(&"webpki-roots")
                                && roots.iter().any(|other| {
                                    let target = other
                                        .get("version")
                                        .and_then(toml::Value::as_str)
                                        .unwrap_or("");
                                    target != version
                                        && (words.len() == 1 || words.get(1) == Some(&target))
                                })
                        })
                    });
                if delegates {
                    continue;
                }
                let source = p.get("source").and_then(toml::Value::as_str).unwrap_or("");
                if !source.starts_with("registry+") || !source.contains("crates.io") {
                    return Err(format!("{file}: webpki-roots is not from crates.io"));
                }
                pins.insert(pin(file, "webpki-roots", version)?);
            }
        } else if Path::new(file).file_name().and_then(|s| s.to_str()) == Some("pyproject.toml") {
            python_toml(
                file,
                &content
                    .parse::<toml::Value>()
                    .map_err(|e| format!("{file}: {e}"))?,
                &mut pins,
            )?;
        } else {
            for line in content.lines() {
                if let Some(p) = requirement(file, line)? {
                    pins.insert(p);
                }
            }
        }
    }
    if pins.is_empty() {
        return Err("No bundled trust-list pins found; refusing to report current".into());
    }
    Ok(pins.into_iter().collect())
}
fn snapshot(repo: &str, reference: &str) -> Result<(String, BTreeMap<String, String>)> {
    let sha = git(
        repo,
        &["rev-parse", "--verify", &format!("{reference}^{{commit}}")],
    )?
    .trim()
    .to_owned();
    let mut files = BTreeMap::new();
    for file in git(repo, &["ls-tree", "-r", "--name-only", "-z", &sha])?
        .split('\0')
        .filter(|s| !s.is_empty())
    {
        let name = Path::new(file)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("");
        if name == "Cargo.lock"
            || name == "pyproject.toml"
            || (name.starts_with("requirements") && name.ends_with(".txt"))
        {
            files.insert(
                file.to_string(),
                git(repo, &["show", &format!("{sha}:{file}")])?,
            );
        }
    }
    Ok((sha, files))
}

// Both publishers include PEM certificates with Subject/Label comments. Compare actual
// certificate bytes, never root counts or release notes. An unfamiliar format is an error.
type Roots = BTreeMap<String, String>;
fn roots(source: &str, rust: bool) -> Result<Roots> {
    let mut roots = Roots::new();
    let mut name = String::new();
    let mut pem: Option<String> = None;
    for raw in source.lines() {
        let s = raw
            .trim()
            .trim_start_matches('#')
            .trim_start_matches('*')
            .trim();
        if let Some(label) = s.strip_prefix("Label:") {
            name = label.trim().trim_matches('"').into();
        } else if let Some(subject) = s.strip_prefix("Subject:") {
            name = subject.trim().into();
        } else if s == "-----BEGIN CERTIFICATE-----" {
            pem = Some(String::new());
        } else if s == "-----END CERTIFICATE-----" {
            let cert = pem.take().ok_or("Certificate END without BEGIN")?;
            if cert.is_empty() || name.is_empty() {
                return Err("Certificate missing bytes or label".into());
            }
            roots.insert(cert, name.clone());
            name.clear();
        } else if let Some(cert) = pem.as_mut() {
            if !s
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))
            {
                return Err("Malformed PEM".into());
            }
            cert.push_str(s);
        }
    }
    if pem.is_some()
        || roots.is_empty()
        || (rust && source.matches("TrustAnchor {").count() != roots.len())
    {
        return Err("Unrecognized trust-list format; cannot establish root diff".into());
    }
    Ok(roots)
}
trait Registry {
    fn latest(&mut self, package: &str) -> Result<String>;
    fn roots(&mut self, package: &str, version: &str) -> Result<Roots>;
}
struct Published;
impl Registry for Published {
    fn latest(&mut self, package: &str) -> Result<String> {
        let version = if package == "webpki-roots" {
            let response = get_json("https://crates.io/api/v1/crates/webpki-roots")?;
            string(&response["crate"], "max_stable_version")?.to_string()
        } else {
            string(
                &get_json("https://pypi.org/pypi/certifi/json")?["info"],
                "version",
            )?
            .to_string()
        };
        pin("registry", package, &version)?;
        Ok(version)
    }
    fn roots(&mut self, package: &str, version: &str) -> Result<Roots> {
        let (url, checksum, path) = if package == "webpki-roots" {
            let meta = get_json(&format!(
                "https://crates.io/api/v1/crates/webpki-roots/{version}"
            ))?;
            (
                format!(
                    "https://static.crates.io/crates/webpki-roots/webpki-roots-{version}.crate"
                ),
                string(&meta["version"], "checksum")?.to_owned(),
                format!("webpki-roots-{version}/src/lib.rs"),
            )
        } else {
            let meta = get_json(&format!("https://pypi.org/pypi/certifi/{version}/json"))?;
            let sdist = meta["urls"]
                .as_array()
                .ok_or("Missing PyPI files")?
                .iter()
                .find(|v| v["packagetype"] == "sdist")
                .ok_or("Missing certifi source distribution")?;
            (
                string(sdist, "url")?.to_owned(),
                string(&sdist["digests"], "sha256")?.to_owned(),
                format!("certifi-{version}/certifi/cacert.pem"),
            )
        };
        let archive = download(&url)?;
        if digest(&archive) != checksum {
            return Err(format!("{package} {version}: registry checksum mismatch"));
        }
        roots(&member(&archive, &path)?, package == "webpki-roots")
    }
}
#[derive(Debug, Serialize)]
struct Finding {
    pin: Pin,
    latest: String,
    removed: Vec<String>,
}
#[derive(Debug, Serialize)]
struct Report {
    revision: String,
    checked: Vec<Pin>,
    findings: Vec<Finding>,
}
fn scan(revision: String, pins: Vec<Pin>, registry: &mut impl Registry) -> Result<Report> {
    let mut latest = BTreeMap::new();
    let mut bundles = BTreeMap::new();
    let mut findings = Vec::new();
    for pin in &pins {
        if !latest.contains_key(&pin.package) {
            latest.insert(pin.package.clone(), registry.latest(&pin.package)?);
        }
        let new = &latest[&pin.package];
        let numbers = |s: &str| {
            s.split('.')
                .map(|p| p.parse::<u64>().map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>>>()
        };
        if numbers(&pin.version)? >= numbers(new)? {
            continue;
        }
        for v in [&pin.version, new] {
            let key = (pin.package.clone(), v.clone());
            if let std::collections::btree_map::Entry::Vacant(slot) = bundles.entry(key) {
                slot.insert(registry.roots(&pin.package, v)?);
            }
        }
        let old_roots = &bundles[&(pin.package.clone(), pin.version.clone())];
        let new_roots = &bundles[&(pin.package.clone(), new.clone())];
        let removed = old_roots
            .iter()
            .filter(|(k, _)| !new_roots.contains_key(*k))
            .map(|(_, name)| name.clone())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        findings.push(Finding {
            pin: pin.clone(),
            latest: new.clone(),
            removed,
        });
    }
    Ok(Report {
        revision,
        checked: pins,
        findings,
    })
}
impl Report {
    fn urgent(&self) -> bool {
        self.findings.iter().any(|f| !f.removed.is_empty())
    }
    fn note(&self) -> String {
        let mut body = format!(
            "{MARKER}\nChecked repository revision {} against crates.io and PyPI.\n",
            self.revision
        );
        for f in &self.findings {
            body += &format!(
                "\n- {}: {} {} -> {}",
                f.pin.file, f.pin.package, f.pin.version, f.latest
            );
            if !f.removed.is_empty() {
                body += &format!("\n  Removed/distrusted roots: {}.", f.removed.join("; "));
            }
        }
        if self.urgent() {
            body += "\n\nURGENT: a newer bundled list removes roots. Bump and ship in the next release.";
        } else {
            body += "\n\nRoutine dependency update: no roots removed in the compared bundles.";
        }
        body
    }
}
trait Hub {
    fn call(&mut self, args: &[&str], key: Option<&str>) -> Result<Value>;
}
struct Cloud;
impl Hub for Cloud {
    fn call(&mut self, args: &[&str], key: Option<&str>) -> Result<Value> {
        hub(args, key)
    }
}
// Only Upgrades' runner lease may write. The cloud fences a lease when its single
// computer assignment changes, so every accepted writer shares this host lock.
// Keep the inode outside checkouts and never unlink it (even after completion).
fn writer_lock(hub: &mut impl Hub, path: &Path) -> Result<File> {
    let who = hub.call(&["whoami"], None)?;
    let assigned = hub.call(&["api", "GET", "bots/upgrades"], None)?;
    if who["actor"] != "bot:upgrades"
        || who["role"] != "bot"
        || who["agent"].as_str().is_some_and(|s| !s.is_empty())
        || who["run_id"].as_str().is_none_or(str::is_empty)
        || who["runner_id"].as_str().is_none_or(str::is_empty)
        || who["runner_id"] != assigned["assignment"]["runner_id"]
        || assigned["state"] != "active"
    {
        return Err("Writing requires Upgrades' current assigned runner lease".into());
    }
    #[cfg(not(unix))]
    return Err("Writing is supported only on the assigned Unix runner host".into());
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .open(path)
            .map_err(|e| format!("writer lock: {e}"))?;
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(file),
                Err(std::fs::TryLockError::WouldBlock) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(e) => return Err(format!("writer lock unavailable; no write attempted: {e}")),
            }
        }
    }
}
fn apply(report: &Report, hub: &mut impl Hub) -> Result<Value> {
    if report.findings.is_empty() {
        return Ok(json!({"action": "none", "reason": "all current"}));
    }
    // Runner sandboxes share HOME; their /tmp directories can be isolated.
    let home = env::var_os("HOME").ok_or("Missing shared runner HOME for writer lock")?;
    let dir = Path::new(&home).join(".cache/tico-trust-list-check");
    std::fs::create_dir_all(&dir).map_err(|e| format!("writer lock directory: {e}"))?;
    apply_at(report, hub, &dir.join("upgrades.lock"))
}
fn apply_at(report: &Report, hub: &mut impl Hub, path: &Path) -> Result<Value> {
    if report.findings.is_empty() {
        return Ok(json!({"action": "none", "reason": "all current"}));
    }
    let _writer = writer_lock(hub, path)?;
    let bot = hub.call(&["api", "GET", "bots/engineering-lead"], None)?;
    if bot["state"] != "active" {
        return Err("engineering-lead is not active; no write attempted".into());
    }
    // The CLI list convenience command discards pagination. Read complete cloud pages,
    // including snoozed tasks and every factory step; never use a local database.
    let mut tasks = Vec::new();
    let mut offset = 0;
    loop {
        let page = hub.call(
            &[
                "api",
                "GET",
                &format!(
                    "tasks?owner=bot%3Aengineering-lead&snoozed=all&limit=100&offset={offset}"
                ),
            ],
            None,
        )?;
        tasks.extend(
            page["tasks"]
                .as_array()
                .ok_or("Unexpected task list response")?
                .iter()
                .cloned(),
        );
        if !page
            .as_object()
            .is_some_and(|v| v.contains_key("next_offset"))
        {
            return Err("Missing task pagination metadata".into());
        }
        match page["next_offset"].as_u64() {
            None if page["next_offset"].is_null() => break,
            Some(next) if next > offset => offset = next,
            _ => return Err("Invalid task pagination offset".into()),
        }
    }
    let matching: Vec<_> = tasks
        .iter()
        .filter(|t| t["body"].as_str().unwrap_or("").starts_with(MARKER))
        .collect();
    let open: Vec<_> = matching
        .iter()
        .filter(|t| !matches!(t["status"].as_str(), Some("done" | "closed" | "declined")))
        .collect();
    if open.len() > 1 {
        return Err("Multiple open trust-list tickets; reconcile them before writing".into());
    }
    let mut note = report.note();
    // An older routine snapshot must not erase a concurrent removal report.
    const EVIDENCE: &str = "\n\nRetained root-removal evidence:\n";
    if !report.urgent() {
        if let Some(task) = open
            .first()
            .filter(|t| t["title"].as_str().unwrap_or("").starts_with("URGENT:"))
        {
            let previous = task["note"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| task["body"].as_str().unwrap_or(""));
            let evidence = previous
                .split_once(EVIDENCE)
                .map_or(previous, |(_, evidence)| evidence);
            note += EVIDENCE;
            note += evidence;
        }
    }
    let urgent = report.urgent()
        || open
            .first()
            .is_some_and(|t| t["title"].as_str().unwrap_or("").starts_with("URGENT:"));
    let title = format!("{}{TITLE}", if urgent { "URGENT: " } else { "" });
    if let Some(task) = open.first() {
        let id = string(task, "id")?;
        if task["note"] == note && task["title"] == title {
            return Ok(json!({"action": "unchanged", "task": id}));
        }
        let key = format!("trust-list:update:{id}:{}", digest(note.as_bytes()));
        let saved = hub.call(
            &["task", "update", id, "--note", &note, "--title", &title],
            Some(&key),
        )?;
        Ok(json!({"action": "updated", "task": saved["id"]}))
    } else {
        // The host lock excludes other attempts through discovery and creation.
        // This key protects retries within one attempt only; cloud receipts are attempt-scoped.
        let generation = matching
            .iter()
            .filter_map(|t| Some((t["created"].as_str()?, t["id"].as_str()?)))
            .max()
            .map(|(_, id)| id)
            .unwrap_or("initial");
        let key = format!("trust-list:create:after:{generation}");
        let mut args = vec![
            "task",
            "create",
            "--owner",
            OWNER,
            "--type",
            "Dev tickets",
            "--step",
            "On Deck",
            "--visibility",
            "private",
            "--title",
            &title,
            "--body",
            &note,
        ];
        if urgent {
            args.push("--top");
        }
        let saved = hub.call(&args, Some(&key))?;
        Ok(json!({"action": "created", "task": saved["id"]}))
    }
}
fn main() {
    if let Err(e) = entry() {
        eprintln!("Trust-list check incomplete: {e}");
        process::exit(1);
    }
}
fn entry() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.iter().any(|s| s == "--help") {
        println!("tico-trust-list-check [--repo PATH] [--ref REF] [--apply]\nDefault: read HEAD, print JSON, no Hub writes. --apply creates or updates one private Dev ticket through hub.");
        return Ok(());
    }
    let (mut repo, mut reference, mut write) = (".".to_string(), "HEAD".to_string(), false);
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--repo" | "--ref" => {
                let v = args.get(i + 1).ok_or("Missing argument")?.clone();
                if args[i] == "--repo" {
                    repo = v;
                } else {
                    reference = v;
                }
                i += 2;
            }
            "--apply" => {
                write = true;
                i += 1;
            }
            _ => return Err(format!("Unknown argument {}", args[i])),
        }
    }
    let (revision, files) = snapshot(&repo, &reference)?;
    let report = scan(revision, inventory(&files)?, &mut Published)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?
    );
    if write {
        println!("{}", apply(&report, &mut Cloud)?);
    }
    Ok(())
}

#[cfg(test)]
mod tests;
