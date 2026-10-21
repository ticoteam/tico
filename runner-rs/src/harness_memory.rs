//! One memory per bot, in its repository (docs/learnings.md, One memory). No turn keeps Claude Code's auto memory any
//! more (hosts/claude.rs turn_env), so what a bot saved there before, a MEMORY.md and one file per memory under the
//! turn's HOME (`.claude/projects/<the bot's checkout, every other character a dash>/memory/`), is copied once into
//! the turn's `memory/learnings.md` as a working-rules section; the turn reads it and the post-turn commit keeps it.
//! The old folder is left as it is, with a `.tico-moved` file saying where its memories went, and never read again.

use std::io::Write;
use std::path::{Path, PathBuf};

pub const MOVED: &str = ".tico-moved";
pub const HEADING: &str = "## Working rules from Claude's memory on this computer";

/// Claude's auto-memory folder for a checkout, under `home`: the path with each character that is not a letter or a
/// digit made a dash, as Claude names its project folders.
pub fn claude_dir(home: &Path, checkout: &Path) -> PathBuf {
    let name: String = checkout.display().to_string().chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '-' }).collect();
    home.join(".claude").join("projects").join(name).join("memory")
}

/// The memories in `dir`, by file name: (file, name, description, body). MEMORY.md, the index, is not one of them.
fn memories(dir: &Path) -> Vec<(String, String, String, String)> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir).into_iter().flatten().flatten().map(|e| e.path())
        .filter(|p| p.is_file() && p.extension().is_some_and(|e| e == "md") && p.file_name().is_some_and(|n| n != "MEMORY.md"))
        .collect();
    files.sort();
    files.iter().filter_map(|p| {
        let text = std::fs::read_to_string(p).ok()?;
        let file = p.file_name()?.to_string_lossy().to_string();
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
        Some((file, name, description, body))
    }).collect()
}

/// The index lines (`- ...`) that name no memory file: a memory written straight into MEMORY.md.
fn index_only(dir: &Path, files: &[String]) -> Vec<String> {
    let text = std::fs::read_to_string(dir.join("MEMORY.md")).unwrap_or_default();
    text.lines().filter(|l| l.starts_with("- ") && !files.iter().any(|f| l.contains(&format!("({f})")))).map(String::from).collect()
}

/// The section for `memory/learnings.md`: each memory one entry, its text indented under it so no line of it reads
/// as a heading or as an entry of its own.
pub fn section(dir: &Path, day: &str) -> Option<(String, usize)> {
    let found = memories(dir);
    let files: Vec<String> = found.iter().map(|m| m.0.clone()).collect();
    let loose = index_only(dir, &files);
    if found.is_empty() && loose.is_empty() {
        return None;
    }
    let mut out = vec![HEADING.to_string(), format!(
        "Moved here by Tico on {day} from Claude's own memory on the computer this bot ran on, which no turn reads any more. Edit or remove them like any other line here.")];
    for (file, name, description, body) in &found {
        let head = if description.is_empty() { format!("- **{name}**") } else { format!("- **{name}**: {description}") };
        out.push(format!("{head} [source: {file}; added: {day}]"));
        out.extend(body.lines().filter(|l| !l.trim().is_empty()).map(|l| format!("  {l}")));
    }
    out.extend(loose.iter().cloned());
    Some((out.join("\n") + "\n", found.len() + loose.len()))
}

/// Puts the section in `learnings` (created if missing): after its title and intro, ahead of its first `## ` section.
pub fn insert(learnings: &str, section: &str) -> String {
    if learnings.trim().is_empty() {
        return format!("# Learnings\n\n{section}");
    }
    let mut at = learnings.len();
    let mut offset = 0;
    for line in learnings.split_inclusive('\n') {
        if line.starts_with("## ") {
            at = offset;
            break;
        }
        offset += line.len();
    }
    let (before, after) = learnings.split_at(at);
    let gap = if before.ends_with("\n\n") || before.is_empty() { "" } else if before.ends_with('\n') { "\n" } else { "\n\n" };
    let tail = if after.is_empty() { String::new() } else { format!("\n{after}") };
    format!("{before}{gap}{section}{tail}")
}

/// Copies the bot's Claude memories for `main` (its own checkout, the folder Claude named them by) into
/// `checkout/memory/learnings.md` once: the number copied, 0 when there were none or another turn already took them.
pub fn migrate(home: &Path, main: &Path, checkout: &Path, day: &str) -> std::io::Result<usize> {
    let dir = claude_dir(home, main);
    if !dir.is_dir() || dir.join(MOVED).exists() {
        return Ok(0);
    }
    let Some((text, count)) = section(&dir, day) else { return Ok(0) };
    // Claimed first, so two turns of one bot never both copy them.
    let mut claim = match std::fs::OpenOptions::new().write(true).create_new(true).open(dir.join(MOVED)) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => return Ok(0),
        Err(e) => return Err(e),
    };
    let target = checkout.join("memory").join("learnings.md");
    let wrote = (|| {
        std::fs::create_dir_all(target.parent().unwrap())?;
        let current = std::fs::read_to_string(&target).unwrap_or_default();
        std::fs::write(&target, insert(&current, &text))
    })();
    if let Err(e) = wrote {
        drop(claim);
        let _ = std::fs::remove_file(dir.join(MOVED));
        return Err(e);
    }
    let _ = writeln!(claim, "{count} memories copied on {day} into {}", target.display());
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("harness-memory-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_bots_claude_memories_move_into_its_learnings_once() {
        let root = temp();
        let (home, main, session) = (root.join("home"), root.join("work/emp-acme"), root.join("work/sessions/acme/t1"));
        std::fs::create_dir_all(&session).unwrap();
        let dir = claude_dir(&home, &main);
        assert!(dir.ends_with(format!(".claude/projects/{}/memory", main.display().to_string().replace(['/', '.', '_'], "-"))));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("MEMORY.md"), "- [Ask first](ask-first.md) — ask before you spec\n- Never deploy on Fridays\n").unwrap();
        std::fs::write(dir.join("ask-first.md"), "---\nname: ask-first\ndescription: Ask 3-5 questions before a spec\nmetadata:\n  type: feedback\n---\n\nAsk first.\n## Not a heading\n- not an entry\n\n**Why:** Chris said so.\n").unwrap();
        std::fs::write(dir.join("plain.md"), "Merge only at the reviewed head.\n").unwrap();
        std::fs::create_dir_all(session.join("memory")).unwrap();
        std::fs::write(session.join("memory/learnings.md"), "# Learnings\nDate each entry.\n\n## 2026-10-05 — Old\n- old\n").unwrap();

        assert_eq!(migrate(&home, &main, &session, "2026-10-21").unwrap(), 3);
        let got = std::fs::read_to_string(session.join("memory/learnings.md")).unwrap();
        assert_eq!(got, format!("# Learnings\nDate each entry.\n\n{HEADING}\nMoved here by Tico on 2026-10-21 from Claude's own memory on the computer this bot ran on, which no turn reads any more. Edit or remove them like any other line here.\n\
- **ask-first**: Ask 3-5 questions before a spec [source: ask-first.md; added: 2026-10-21]\n  Ask first.\n  ## Not a heading\n  - not an entry\n  **Why:** Chris said so.\n\
- **plain** [source: plain.md; added: 2026-10-21]\n  Merge only at the reviewed head.\n\
- Never deploy on Fridays\n\n## 2026-10-05 — Old\n- old\n"));
        // Once: a second turn, here or in another checkout, copies nothing; the old folder stays, marked.
        assert_eq!(migrate(&home, &main, &main, "2026-10-22").unwrap(), 0);
        assert!(!main.join("memory/learnings.md").exists());
        assert!(dir.join("ask-first.md").exists() && std::fs::read_to_string(dir.join(MOVED)).unwrap().starts_with("3 memories copied"));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn no_claude_memory_changes_nothing_and_a_new_file_gets_a_title() {
        let root = temp();
        let (home, main) = (root.join("home"), root.join("emp-acme"));
        assert_eq!(migrate(&home, &main, &main, "2026-10-21").unwrap(), 0);
        std::fs::create_dir_all(claude_dir(&home, &main)).unwrap();
        assert_eq!(migrate(&home, &main, &main, "2026-10-21").unwrap(), 0, "an empty folder");
        assert!(!claude_dir(&home, &main).join(MOVED).exists() && !main.join("memory").exists());
        std::fs::write(claude_dir(&home, &main).join("MEMORY.md"), "- One rule\n").unwrap();
        assert_eq!(migrate(&home, &main, &main, "2026-10-21").unwrap(), 1);
        assert!(std::fs::read_to_string(main.join("memory/learnings.md")).unwrap().starts_with(&format!("# Learnings\n\n{HEADING}\n")));
        assert_eq!(insert("# L\nintro", "S\n"), "# L\nintro\n\nS\n");
        std::fs::remove_dir_all(root).unwrap();
    }
}
