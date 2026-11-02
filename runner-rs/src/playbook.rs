//! A routine's text is saved once, so an edited playbook never reaches it (a bot's checks once ran an old text
//! for a week). When the text is an older copy of one of the bot's `playbooks/*.md`, the prompt says to follow
//! the current file (service.py `stale_playbook`, `stale_playbook_note`).

use std::collections::HashMap;
use std::path::Path;

use regex::Regex;
use serde_json::Value;

use crate::util::text_of;

/// The playbook (`playbooks/<name>.md`) a routine's text is an out-of-date copy of, or None. A short text that
/// names a playbook is a pointer: the bot reads the current file itself. A long text is a copy when it starts
/// with the same heading as a playbook or reads mostly like one; it is stale when that file no longer says the same.
pub fn stale_playbook(repo: &Path, text: &str) -> Option<String> {
    let folder = repo.join("playbooks");
    if text.trim().is_empty() || !folder.is_dir() {
        return None;
    }
    let reference = Regex::new(r"playbooks/[\w.-]+\.md").unwrap();
    if reference.is_match(text) && text.chars().count() < 600 {
        return None;
    }
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let heading = text.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or_default();
    let mut files: Vec<_> = std::fs::read_dir(&folder).ok()?.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "md") && p.is_file()).collect();
    files.sort();
    let head: Vec<char> = flat.chars().take(4000).collect();
    let mut best: Option<(String, f64)> = None;
    for path in files {
        let current = String::from_utf8_lossy(&std::fs::read(&path).ok()?).into_owned();
        let flat_current = current.split_whitespace().collect::<Vec<_>>().join(" ");
        if !flat_current.is_empty() && flat.contains(&flat_current) {
            return None; // the text holds the current file: nothing is stale
        }
        let first = current.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or_default();
        let ratio = ratio(&head, &flat_current.chars().take(4000).collect::<Vec<_>>());
        let like = (heading.starts_with('#') && heading == first) || ratio >= 0.6;
        if like && best.as_ref().is_none_or(|(_, r)| ratio > *r) {
            best = Some((format!("playbooks/{}", path.file_name()?.to_string_lossy()), ratio));
        }
    }
    best.map(|(p, _)| p)
}

/// What to tell a routine whose text is an older copy of one of the bot's playbooks, or "".
pub fn note(repo: &Path, attempt: &Value) -> String {
    if !crate::util::truthy(Some(&attempt["routine"])) {
        return String::new();
    }
    let Some(path) = stale_playbook(repo, &text_of(&attempt["task"]["body"])) else { return String::new() };
    // Routines are automations now (`hub automation`): one that moved names its automation; its text is the prompt of
    // its turn step, changed by saving the steps again.
    let routine = &attempt["routine"];
    let id = [text_of(&routine["automation"]), text_of(&routine["id"])].into_iter().find(|i| !i.is_empty()).unwrap_or_else(|| "<id>".into());
    format!(
        "This routine's text is an older copy of `{path}`, which has changed since the routine was saved. Follow the current `{path}` in your repository, not the task text. Then point the routine at the file so it cannot go stale again: read it with `hub automation show {id}`, set its bot_turn step's prompt to `Run {path}`, and save the steps with `hub automation update {id} --file <flow.json>`."
    )
}

/// difflib.SequenceMatcher(None, a, b).ratio(), autojunk included: in a `b` of 200 or more items, an item that
/// makes up more than 1% of it is not used to start a match (it still extends one).
pub fn ratio(a: &[char], b: &[char]) -> f64 {
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    if b.len() >= 200 {
        let most = b.len() / 100 + 1;
        b2j.retain(|_, js| js.len() <= most);
    }
    let longest = |alo: usize, ahi: usize, blo: usize, bhi: usize| -> (usize, usize, usize) {
        let (mut besti, mut bestj, mut size) = (alo, blo, 0);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut next: HashMap<usize, usize> = HashMap::new();
            for &j in b2j.get(c).map(Vec::as_slice).unwrap_or_default() {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let k = if j > 0 { j2len.get(&(j - 1)).copied().unwrap_or(0) } else { 0 } + 1;
                next.insert(j, k);
                if k > size {
                    (besti, bestj, size) = (i + 1 - k, j + 1 - k, k);
                }
            }
            j2len = next;
        }
        while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
            (besti, bestj, size) = (besti - 1, bestj - 1, size + 1);
        }
        while besti + size < ahi && bestj + size < bhi && a[besti + size] == b[bestj + size] {
            size += 1;
        }
        (besti, bestj, size)
    };
    let mut matched = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest(alo, ahi, blo, bhi);
        if k > 0 {
            matched += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    2.0 * matched as f64 / (a.len() + b.len()) as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn r(a: &str, b: &str) -> f64 {
        ratio(&a.chars().collect::<Vec<_>>(), &b.chars().collect::<Vec<_>>())
    }

    #[test]
    fn ratios_match_pythons_difflib() {
        assert_eq!(r("abcd", "bcde"), 0.75);
        assert_eq!(r("", ""), 1.0);
        // difflib.SequenceMatcher(None, "private Thread currentThread;", "private volatile Thread currentThread;").ratio()
        assert!((r("private Thread currentThread;", "private volatile Thread currentThread;") - 0.8656716417910447).abs() < 1e-12);
        // autojunk: a 300-char b of mostly "a" (python: 0.013201320132013201)
        let b = format!("{}{}", "a".repeat(298), "xy");
        assert!((r("xyz", &b) - 0.013201320132013201).abs() < 1e-12);
    }

    #[test]
    fn a_routine_holding_an_old_copy_of_a_playbook_is_told_to_follow_the_file() {
        let dir = std::env::temp_dir().join(format!("tico-playbook-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(dir.join("playbooks")).unwrap();
        std::fs::write(dir.join("playbooks").join("checks.md"), "# Daily checks\n\nLook at the queue, then the inbox, then report.\n").unwrap();
        let old = "# Daily checks\n\nLook at the queue and report.\n";
        let attempt = json!({"routine": {"id": "r1"}, "task": {"body": old}});
        let told = note(&dir, &attempt);
        assert!(told.contains("hub automation show r1") && told.contains("prompt to `Run playbooks/checks.md`"), "{told}");
        assert!(told.contains("hub automation update r1 --file"), "{told}");
        // a routine that moved to an automation is changed there
        let moved = json!({"routine": {"id": "r1", "automation": "auto_1"}, "task": {"body": old}});
        assert!(note(&dir, &moved).contains("hub automation update auto_1 --file"));
        assert_eq!(note(&dir, &json!({"routine": {"id": "r1"}, "task": {"body": "Run playbooks/checks.md"}})), "");
        let current = std::fs::read_to_string(dir.join("playbooks").join("checks.md")).unwrap();
        assert_eq!(note(&dir, &json!({"routine": {"id": "r1"}, "task": {"body": current}})), "");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
