//! Once per placement, before a bot's first turn on this computer, a clean checkout that tracks GitHub is brought
//! up to GitHub's history (service.py `catch_up`, git_credentials.fast_forward). A computer that hosted the bot
//! before still holds the clone it left with; without this the bot would work from that stale copy. A copy with
//! local changes, or one that diverged, is kept as it is and says so on the bot (Settings, Health).

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Mutex, OnceLock};

use serde_json::Value;

use crate::env::Env;
use crate::util::log;

pub const KEPT_WARNING: &str = "Not brought up to GitHub's history: ";

#[derive(Default)]
struct Placements {
    /// bot -> the placement generation it was caught up for.
    done: HashMap<String, Value>,
    /// bot -> why its copy was kept as it is.
    notes: HashMap<String, String>,
}

fn placements() -> &'static Mutex<Placements> {
    static P: OnceLock<Mutex<Placements>> = OnceLock::new();
    P.get_or_init(Default::default)
}

/// Why the bot's copy was kept rather than brought up to GitHub's, for its readiness warnings.
pub fn kept(bot: &str) -> Option<String> {
    placements().lock().unwrap().notes.get(bot).cloned()
}

async fn git(path: &Path, env: &Env, args: &[&str], timeout_s: u64) -> Result<(bool, String), &'static str> {
    match crate::gitrun::git_as_bot(path, env, args, timeout_s).await {
        Err(crate::gitrun::Fault::Timeout(_)) => Err("timed out"),
        Err(_) => Err("Git command could not run"),
        Ok(out) => Ok((out.ok(), out.stdout)),
    }
}

/// (state, detail): `updated`, `current` (already there, or ahead), `skipped` (no upstream to follow) or `kept`
/// (local changes, diverged, or GitHub unreachable; the copy is left exactly as it was).
pub async fn fast_forward(path: &Path, turn: &Env) -> (&'static str, String) {
    let env = crate::safegit::environment(turn);
    let steps = async {
        if !git(path, &env, &["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 15).await?.0 {
            return Ok(("skipped", "no upstream"));
        }
        if !git(path, &env, &["fetch", "--quiet", "--no-tags"], 60).await?.0 {
            return Ok(("kept", "could not reach GitHub"));
        }
        if git(path, &env, &["merge-base", "--is-ancestor", "@{u}", "HEAD"], 15).await?.0 {
            return Ok(("current", ""));
        }
        if !git(path, &env, &["merge-base", "--is-ancestor", "HEAD", "@{u}"], 15).await?.0 {
            return Ok(("kept", "it has commits GitHub does not, and GitHub has commits it does not"));
        }
        crate::repositories::repair_instruction_links(path, &env).await;
        let (ok, status) = git(path, &env, &["status", "--porcelain", "--untracked-files=no"], 15).await?;
        if !ok { return Err("could not inspect local changes".to_string()); }
        if !status.trim().is_empty() {
            return Err(format!("it has uncommitted changes: {}", crate::repositories::changed_file_names(&status)));
        }
        let merged = git(path, &env, &["merge", "--ff-only", "--quiet", "@{u}"], 60).await?.0;
        Ok::<_, String>(if merged { ("updated", "") } else { ("kept", "fast-forward failed") })
    };
    match steps.await {
        Ok((state, detail)) => (state, detail.to_string()),
        Err(why) => ("kept", why.to_string()),
    }
}

/// Before a turn in the bot's checkout: catch the copy up once per placement (`generation`).
pub async fn catch_up(bot: &str, generation: &Value, path: &Path, env: &Env) {
    if placements().lock().unwrap().done.get(bot) == Some(generation) || !path.join(".git").exists() {
        return;
    }
    let (state, detail) = fast_forward(path, env).await;
    let mut p = placements().lock().unwrap();
    if detail != "could not reach GitHub" {
        p.done.insert(bot.to_string(), generation.clone()); // an unreachable GitHub is asked again before the next turn
    }
    if state == "updated" {
        crate::isolation::adopt(&[path.to_path_buf()]); // updated by the supervisor; the bot user works in it
        log(&format!("Tico runner: {bot}: brought up to GitHub's history before its first turn here"));
    }
    if state == "kept" {
        log(&format!("Tico runner: {bot}: kept this computer's copy as it is, not GitHub's latest ({detail})"));
        p.notes.insert(bot.to_string(), detail);
    } else {
        p.notes.remove(bot);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn run(dir: &Path, args: &[&str]) {
        let ok = Command::new("git").args(args).current_dir(dir).env("GIT_AUTHOR_NAME", "t").env("GIT_AUTHOR_EMAIL", "t@t.invalid").env("GIT_COMMITTER_NAME", "t").env("GIT_COMMITTER_EMAIL", "t@t.invalid").output().unwrap();
        assert!(ok.status.success(), "{args:?}: {}", String::from_utf8_lossy(&ok.stderr));
    }

    #[tokio::test]
    async fn a_returning_bots_old_clone_is_brought_up_once_and_a_diverged_one_is_kept() {
        let root = std::env::temp_dir().join(format!("tico-placement-{}", uuid::Uuid::new_v4().simple()));
        let (origin, here, other) = (root.join("origin.git"), root.join("here"), root.join("other"));
        std::fs::create_dir_all(&root).unwrap();
        run(&root, &["init", "-q", "--bare", "-b", "main", origin.to_str().unwrap()]);
        run(&root, &["clone", "-q", origin.to_str().unwrap(), other.to_str().unwrap()]);
        run(&other, &["commit", "-q", "--allow-empty", "-m", "one"]);
        run(&other, &["push", "-q", "origin", "HEAD:main"]);
        run(&root, &["clone", "-q", origin.to_str().unwrap(), here.to_str().unwrap()]);
        std::fs::write(here.join("untracked.txt"), "x").unwrap(); // untracked files do not keep a copy back
        run(&other, &["commit", "-q", "--allow-empty", "-m", "two"]);
        run(&other, &["push", "-q", "origin", "HEAD:main"]);
        let env = crate::env::process_environment();
        catch_up("coo", &Value::from(1), &here, &env).await;
        assert!(kept("coo").is_none());
        let head = |p: &Path| String::from_utf8(Command::new("git").args(["rev-parse", "HEAD"]).current_dir(p).output().unwrap().stdout).unwrap();
        assert_eq!(head(&here), head(&other));
        run(&here, &["commit", "-q", "--allow-empty", "-m", "local"]);
        run(&other, &["commit", "-q", "--allow-empty", "-m", "three"]);
        run(&other, &["push", "-q", "origin", "HEAD:main"]);
        catch_up("coo", &Value::from(1), &here, &env).await; // the same placement: not asked again
        assert!(kept("coo").is_none());
        catch_up("coo", &Value::from(2), &here, &env).await;
        assert_eq!(kept("coo").as_deref(), Some("it has commits GitHub does not, and GitHub has commits it does not"));
        let _ = std::fs::remove_dir_all(&root);
    }
}
