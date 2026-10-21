//! One memory per bot, in its repository (docs/learnings.md, One memory). No turn reads a harness's own memory any
//! more (hosts/claude.rs turn_env, hosts/codex.rs, hosts/gemini.rs), so what a bot saved there before is copied into
//! the turn's `memory/learnings.md` as a working-rules section: Claude's auto memory (a MEMORY.md and one file per
//! memory under the turn's HOME, `.claude/projects/<the bot's checkout, every other character a dash>/memory/`) and
//! the Gemini CLI's saved memory (its home's `.gemini/GEMINI.md`, set aside unread by hosts/gemini.rs).
//!
//! Every entry carries its source (`claude-memory/<file>`), so a retry adds only what the checkout lacks. The turn's
//! memory commit keeps the copy; the move is done (`done` in the runner's state for the bot) only once the copy is in
//! the repository's committed history, the upstream's when there is one. Until then a claim names the checkout that
//! copied, so another lane does not copy the same entries again; a claim older than CLAIM_SECS is taken over. The old
//! files are left as they are.

use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

pub const HEADING: &str = "## Working rules from Claude's memory on this computer";
const CLAIM_SECS: u64 = 6 * 3600;

pub struct Entry {
    pub tag: String,
    pub text: String,
}

/// Claude's auto-memory folder for a checkout, under `home`: the path with each character that is not a letter or a
/// digit made a dash, as Claude names its project folders.
pub fn claude_dir(home: &Path, checkout: &Path) -> PathBuf {
    let name: String = checkout.display().to_string().chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '-' }).collect();
    home.join(".claude").join("projects").join(name).join("memory")
}

/// One entry: a line naming it, the source tag, and its text indented under it, so no line of it reads as a heading or
/// as an entry of its own. Blank lines are kept as empty indented lines.
fn entry(tag: String, title: &str, body: &str, day: &str) -> Entry {
    let mut lines = vec![format!("- {title} [source: {tag}; added: {day}]")];
    lines.extend(body.trim().lines().map(|l| if l.trim().is_empty() { String::new() } else { format!("  {l}") }));
    Entry { tag, text: lines.join("\n") }
}

/// The memories in Claude's folder: one entry per memory file, then what MEMORY.md says beyond its links to those
/// files. An unreadable file is an error: nothing is copied from a set that cannot be read whole.
pub fn claude_entries(dir: &Path, day: &str) -> std::io::Result<Vec<Entry>> {
    if !dir.is_dir() {
        return Ok(vec![]);
    }
    let mut files: Vec<PathBuf> = vec![];
    for e in std::fs::read_dir(dir)? {
        let p = e?.path();
        if p.is_file() && p.extension().is_some_and(|e| e == "md") && p.file_name().is_some_and(|n| n != "MEMORY.md") {
            files.push(p);
        }
    }
    files.sort();
    let mut out = vec![];
    let mut names = vec![];
    for p in &files {
        let text = std::fs::read_to_string(p)?;
        let file = p.file_name().unwrap_or_default().to_string_lossy().to_string();
        let (mut name, mut description, mut body) = (String::new(), String::new(), text.trim().to_string());
        if let Some(rest) = text.strip_prefix("---\n") {
            if let Some(end) = rest.find("\n---") {
                for line in rest[..end].lines() {
                    if let Some(v) = line.strip_prefix("name:") { name = v.trim().to_string(); }
                    if let Some(v) = line.strip_prefix("description:") { description = v.trim().to_string(); }
                }
                body = rest[end + 4..].trim().to_string();
            }
        }
        if name.is_empty() { name = file.trim_end_matches(".md").to_string(); }
        let title = if description.is_empty() { format!("**{name}**") } else { format!("**{name}**: {description}") };
        out.push(entry(format!("claude-memory/{file}"), &title, &body, day));
        names.push(file);
    }
    let index = dir.join("MEMORY.md");
    if index.exists() {
        // Only a line that is just a link to a copied file (and its one-line hook) is left out: it is that entry.
        let text = std::fs::read_to_string(&index)?;
        let rest: Vec<&str> = text.lines().filter(|l| {
            let t = l.trim_start();
            !(t.starts_with("- [") && names.iter().any(|f| t.contains(&format!("]({f})"))))
        }).collect();
        let rest = rest.join("\n");
        if rest.lines().any(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#')) {
            out.push(entry("claude-memory/MEMORY.md".into(), "**MEMORY.md** (what the index said beyond the memories above)", &rest, day));
        }
    }
    Ok(out)
}

/// The Gemini CLI's saved memory in its home, one entry: what hosts/gemini.rs set aside unread, then a GEMINI.md not
/// yet set aside. One tag for both, so setting it aside between two turns never copies it twice.
pub fn gemini_entries(home: &Path, day: &str) -> std::io::Result<Vec<Entry>> {
    let mut parts = vec![];
    for name in [crate::hosts::gemini::MEMORY_UNREAD, "GEMINI.md"] {
        match std::fs::read_to_string(home.join(".gemini").join(name)) {
            Ok(t) if !t.trim().is_empty() => parts.push(t.trim().to_string()),
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(e),
        }
    }
    Ok(if parts.is_empty() { vec![] } else { vec![entry("gemini-memory/GEMINI.md".into(), "**Gemini saved memory**", &parts.join("\n\n"), day)] })
}

fn has(text: &str, tag: &str) -> bool {
    text.contains(&format!("[source: {tag};"))
}

/// Puts `entries` in `learnings` (created if missing): at the end of the moved-rules section when there is one, else as
/// that section after the title and intro, ahead of the first `## ` section or log entry.
pub fn insert(learnings: &str, entries: &[&Entry], day: &str) -> String {
    let body = entries.iter().map(|e| e.text.as_str()).collect::<Vec<_>>().join("\n") + "\n";
    let lines: Vec<&str> = learnings.split_inclusive('\n').collect();
    let offset = |i: usize| lines[..i].iter().map(|l| l.len()).sum::<usize>();
    let (at, text) = if let Some(h) = lines.iter().position(|l| l.trim_end() == HEADING) {
        let end = lines.iter().skip(h + 1).position(|l| l.starts_with("## ")).map_or(lines.len(), |i| h + 1 + i);
        let mut end = end;
        // Back over the blank lines before the next section, so the new entries follow the last one.
        while end > h + 1 && lines[end - 1].trim().is_empty() {
            end -= 1;
        }
        (offset(end), body)
    } else {
        let section = format!("{HEADING}\nMoved here by Tico on {day} from the AI tool's own memory on the computer this bot ran on, \
which no turn reads any more. Edit or remove them like any other line here.\n{body}");
        if learnings.trim().is_empty() {
            return format!("# Learnings\n\n{section}");
        }
        let first = lines.iter().enumerate().skip(1).find(|(_, l)| {
            l.starts_with("## ") || l.starts_with("- ") || l.starts_with("* ") || l.split_once(". ").is_some_and(|(n, _)| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()))
        }).map_or(lines.len(), |(i, _)| i);
        // Ahead of a flat log's first entry, that log gets a heading of its own, or its entries would read as the
        // moved rules.
        let entry = first < lines.len() && !lines[first].starts_with("## ");
        (offset(first), if entry { section + "\n## Log\n" } else { section + "\n" })
    };
    let (before, after) = learnings.split_at(at);
    let gap = if before.is_empty() || before.ends_with("\n\n") || (before.ends_with('\n') && text.starts_with("- ")) { "" } else if before.ends_with('\n') { "\n" } else { "\n\n" };
    format!("{before}{gap}{text}{after}")
}

/// memory/learnings.md as the repository has committed it: at the upstream when the checkout tracks one, else at HEAD.
fn committed(checkout: &Path) -> Option<String> {
    for rev in ["@{upstream}", "HEAD"] {
        let out = std::process::Command::new("git")
            .args(["-c", "safe.directory=*", "show", &format!("{rev}:memory/learnings.md")])
            .current_dir(checkout)
            .stderr(std::process::Stdio::null())
            .output()
            .ok()?;
        if out.status.success() {
            return Some(String::from_utf8_lossy(&out.stdout).to_string());
        }
        let known = std::process::Command::new("git").args(["-c", "safe.directory=*", "rev-parse", "--verify", "-q", rev])
            .current_dir(checkout).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).status().ok()?;
        if known.success() {
            return Some(String::new()); // that revision has no learnings.md yet
        }
    }
    None
}

fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    let dir = path.parent().ok_or_else(|| std::io::Error::other("no parent"))?;
    std::fs::create_dir_all(dir)?;
    let tmp = dir.join(format!(".{}.tico-{}", path.file_name().unwrap_or_default().to_string_lossy(), uuid::Uuid::new_v4().simple()));
    let wrote = std::fs::write(&tmp, text).and_then(|_| std::fs::File::open(&tmp)?.sync_all()).and_then(|_| std::fs::rename(&tmp, path));
    if wrote.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    wrote
}

#[derive(Debug, PartialEq)]
pub enum Moved {
    /// Nothing to copy, or the copy is already in the repository's history.
    Done,
    /// This many entries were copied into the checkout now (0: already there, waiting for the turn's commit).
    Copied(usize),
    /// Another checkout copied them and its commit has not landed yet.
    Elsewhere,
}

/// Before a turn in `checkout`, a git checkout of the bot whose own checkout is `main`: copies the entries the checkout
/// lacks. `state` is the runner's folder for this bot's move (`done`, `claim`).
pub fn before_turn(entries: std::io::Result<Vec<Entry>>, checkout: &Path, state: &Path, day: &str, now: SystemTime) -> std::io::Result<Moved> {
    if state.join("done").exists() {
        return Ok(Moved::Done);
    }
    let entries = entries?;
    let finish = |why: &str| -> std::io::Result<Moved> {
        write_atomic(&state.join("done"), &format!("{why}\n"))?;
        let _ = std::fs::remove_file(state.join("claim"));
        Ok(Moved::Done)
    };
    if entries.is_empty() {
        return finish("no harness memory");
    }
    if let Some(text) = committed(checkout) {
        if entries.iter().all(|e| has(&text, &e.tag)) {
            return finish(&format!("{} entries in the repository's history", entries.len()));
        }
    }
    let target = checkout.join("memory").join("learnings.md");
    let current = match std::fs::read_to_string(&target) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(e),
    };
    let missing: Vec<&Entry> = entries.iter().filter(|e| !has(&current, &e.tag)).collect();
    if missing.is_empty() {
        return Ok(Moved::Copied(0));
    }
    let here = checkout.display().to_string();
    if let Ok(claim) = std::fs::read_to_string(state.join("claim")) {
        let (secs, path) = claim.split_once(' ').unwrap_or(("0", ""));
        let at = SystemTime::UNIX_EPOCH + Duration::from_secs(secs.trim().parse().unwrap_or(0));
        let fresh = now.duration_since(at).map_or(true, |age| age.as_secs() < CLAIM_SECS);
        if fresh && path.trim() != here {
            return Ok(Moved::Elsewhere);
        }
    }
    let secs = now.duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default().as_secs();
    write_atomic(&state.join("claim"), &format!("{secs} {here}\n"))?;
    write_atomic(&target, &insert(&current, &missing, day))?;
    Ok(Moved::Copied(missing.len()))
}

/// The caller's step (execute.rs, after placement): the bot's Claude memory for its own checkout `main` and its Gemini
/// home's saved memory, into the turn's checkout.
pub fn move_for_turn(home: &Path, main: &Path, checkout: &Path, gemini_home: &Path, state: &Path, day: &str) -> std::io::Result<Moved> {
    if state.join("done").exists() {
        return Ok(Moved::Done);
    }
    let entries = claude_entries(&claude_dir(home, main), day).and_then(|mut e| { e.extend(gemini_entries(gemini_home, day)?); Ok(e) });
    before_turn(entries, checkout, state, day, SystemTime::now())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("harness-memory-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn seed(dir: &Path) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(dir.join("MEMORY.md"), "# Memory\n- [Ask first](ask-first.md) — ask before you spec\n\nAsk Chris before publishing.\n* Never deploy on Fridays\n- Release only when approved\n  Approval must name the exact version.\n").unwrap();
        std::fs::write(dir.join("ask-first.md"), "---\nname: ask-first\ndescription: Ask 3-5 questions before a spec\nmetadata:\n  type: feedback\n---\n\nAsk first.\n## Not a heading\n- not an entry\n\n**Why:** Chris said so.\n").unwrap();
        std::fs::write(dir.join("plain.md"), "Merge only at the reviewed head.\n").unwrap();
    }

    #[test]
    fn every_line_of_claude_memory_is_copied_once_and_nothing_is_marked_done_before_it_is_committed() {
        let root = temp();
        let (dir, checkout, state) = (root.join("mem"), root.join("work"), root.join("state"));
        seed(&dir);
        std::fs::create_dir_all(checkout.join("memory")).unwrap();
        std::fs::write(checkout.join("memory/learnings.md"), "# Learnings\nDate each entry.\n- 2026-10-05: old\n- 2026-10-09: newer\n").unwrap();
        let now = SystemTime::now();
        let got = before_turn(claude_entries(&dir, "2026-10-21"), &checkout, &state, "2026-10-21", now).unwrap();
        assert_eq!(got, Moved::Copied(3));
        let text = std::fs::read_to_string(checkout.join("memory/learnings.md")).unwrap();
        // Rules go ahead of a flat log; every rule and qualification is there; the link line is the file's entry.
        assert!(text.starts_with(&format!("# Learnings\nDate each entry.\n\n{HEADING}\n")), "{text}");
        assert!(text.ends_with("\n\n## Log\n- 2026-10-05: old\n- 2026-10-09: newer\n"), "{text}");
        for line in ["Ask Chris before publishing.", "* Never deploy on Fridays", "- Release only when approved", "    Approval must name the exact version.",
                     "  ## Not a heading", "  - not an entry", "  **Why:** Chris said so.", "Merge only at the reviewed head.",
                     "- **ask-first**: Ask 3-5 questions before a spec [source: claude-memory/ask-first.md; added: 2026-10-21]"] {
            assert!(text.contains(line), "{line}\n{text}");
        }
        assert!(!text.contains("[Ask first](ask-first.md)"));
        assert!(!state.join("done").exists(), "not committed anywhere yet (no git here)");
        // A retry, as after a restart, adds nothing; another checkout defers to the fresh claim.
        assert_eq!(before_turn(claude_entries(&dir, "2026-10-22"), &checkout, &state, "2026-10-22", now).unwrap(), Moved::Copied(0));
        assert_eq!(std::fs::read_to_string(checkout.join("memory/learnings.md")).unwrap(), text);
        let other = root.join("other");
        assert_eq!(before_turn(claude_entries(&dir, "d"), &other, &state, "d", now).unwrap(), Moved::Elsewhere);
        assert!(!other.join("memory").exists());
        // A claim left by a turn that died before writing: the same checkout copies; a stale one is taken over.
        std::fs::write(checkout.join("memory/learnings.md"), "# Learnings\n").unwrap();
        assert_eq!(before_turn(claude_entries(&dir, "d"), &checkout, &state, "d", now).unwrap(), Moved::Copied(3));
        let later = now + Duration::from_secs(CLAIM_SECS + 1);
        assert_eq!(before_turn(claude_entries(&dir, "d"), &other, &state, "d", later).unwrap(), Moved::Copied(3));
        // A source that cannot be read copies nothing and claims nothing.
        let (bad, fresh) = (root.join("bad"), root.join("fresh"));
        seed(&bad);
        std::fs::write(bad.join("broken.md"), [0xff, 0xfe, 0x00]).unwrap();
        let state2 = root.join("state2");
        assert!(before_turn(claude_entries(&bad, "d"), &fresh, &state2, "d", now).is_err());
        assert!(!fresh.join("memory").exists() && !state2.join("claim").exists() && !state2.join("done").exists());
        // No memory at all: done at once, nothing written.
        let state3 = root.join("state3");
        assert_eq!(before_turn(claude_entries(&root.join("none"), "d"), &fresh, &state3, "d", now).unwrap(), Moved::Done);
        assert!(!fresh.join("memory").exists() && state3.join("done").exists());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn a_new_learnings_file_gets_a_title_and_a_second_copy_joins_the_moved_section() {
        let e = |t: &str| Entry { tag: format!("claude-memory/{t}"), text: format!("- {t} [source: claude-memory/{t}; added: d]") };
        let one = insert("", &[&e("a.md")], "d");
        assert!(one.starts_with(&format!("# Learnings\n\n{HEADING}\n")));
        let with_log = format!("{one}\n## 2026-10-01 — Old\n- old\n");
        let two = insert(&with_log, &[&e("b.md")], "d");
        assert!(two.contains("- a.md [source: claude-memory/a.md; added: d]\n- b.md [source: claude-memory/b.md; added: d]\n\n## 2026-10-01"), "{two}");
        assert_eq!(insert("# L\nintro", &[&e("c.md")], "d").matches(HEADING).count(), 1);
    }
}
