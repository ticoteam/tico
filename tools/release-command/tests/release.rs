#![cfg(unix)]
use serde_json::{json, Value};
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new(config: Value) -> Self {
        let root = std::env::temp_dir().join(format!(
            "release-command-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("fixture.json"), config.to_string()).unwrap();
        fs::write(root.join("main"), "a".repeat(40)).unwrap();
        let python = Command::new("python3")
            .args(["-c", "import sys; print(sys.executable)"])
            .output()
            .unwrap();
        let python = String::from_utf8(python.stdout).unwrap();
        for name in ["git", "gh", "python3"] {
            let path = root.join(name);
            fs::write(
                &path,
                format!(
                    "#!/bin/sh\nFAKE_TOOL={name} exec '{}' '{}/tests/fake_tools.py' \"$@\"\n",
                    python.trim(),
                    env!("CARGO_MANIFEST_DIR")
                ),
            )
            .unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
        }
        Self(root)
    }
    fn call(&self, args: &[&str]) -> Output {
        Command::new(
            std::env::var_os("TICO_RELEASE_COMMAND")
                .unwrap_or_else(|| env!("CARGO_BIN_EXE_tico-release").into()),
        )
        .args(args)
        .arg("--state")
        .arg(self.0.join("freeze.json"))
        .env("RELEASE_FIXTURE", &self.0)
        .env("PATH", &self.0)
        .env_remove("HUB_TOKEN")
        .env_remove("GH_TOKEN")
        .output()
        .unwrap()
    }
    fn freeze(&self) -> Output {
        self.call(&["freeze", "--version", "v1.0.1", "--previous", "v1.0.0"])
    }
    fn publish(&self) -> Output {
        self.call(&["publish", "--notes-commit", &"b".repeat(40)])
    }
    fn recover_run(&self) {
        let path = self.0.join("freeze.json");
        let mut state: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        state["candidate"] = json!("b".repeat(40));
        state["run"] = json!(42);
        state["dispatching"] = json!(true);
        fs::write(path, state.to_string()).unwrap();
        fs::write(self.0.join("main"), "b".repeat(40)).unwrap();
        fs::write(self.0.join("watched"), "").unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn passed(out: Output) {
    assert!(
        out.status.success(),
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
}
fn refused(f: &Fixture, out: Output, reason: &str) {
    assert!(!out.status.success());
    assert!(
        String::from_utf8_lossy(&out.stderr).contains(reason),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(!f.0.join("tag").exists());
}
#[test]
fn moved_main_refuses_before_dispatch_and_after_wait() {
    let f = Fixture::new(json!({}));
    passed(f.freeze());
    fs::write(f.0.join("main"), "c".repeat(40)).unwrap();
    refused(&f, f.publish(), "main moved after freeze");
    assert!(!f.0.join("dispatched").exists());
    let f = Fixture::new(json!({"move_during_watch":true}));
    passed(f.freeze());
    refused(&f, f.publish(), "main moved after freeze");
}
#[test]
fn different_candidate_or_title_refuses_even_when_run_is_green() {
    for config in [json!({"wrong_commit":true}), json!({"wrong_title":true})] {
        let f = Fixture::new(config);
        passed(f.freeze());
        f.recover_run();
        refused(&f, f.publish(), "title or candidate commit");
    }
}
#[test]
fn red_nightly_blocks_until_a_newer_full_gate_is_green() {
    let f = Fixture::new(json!({"red":true}));
    refused(&f, f.freeze(), "Latest full gate on main is red");
    assert!(!f.0.join("freeze.json").exists());
    let f = Fixture::new(json!({"red":true,"newer_green":true}));
    passed(f.freeze());
    passed(f.publish());
}
#[test]
fn happy_path_tags_exactly_the_validated_notes_commit_and_resumes_same_run() {
    let f = Fixture::new(json!({}));
    passed(f.freeze());
    passed(f.publish());
    let tag: Value = serde_json::from_slice(&fs::read(f.0.join("tag")).unwrap()).unwrap();
    assert_eq!(tag, json!({"sha":"b".repeat(40),"ref":"refs/tags/v1.0.1"}));
    fs::remove_file(f.0.join("dispatched")).unwrap();
    passed(f.publish());
    assert!(!f.0.join("dispatched").exists());
}
#[test]
fn post_freeze_code_and_failed_validation_never_tag() {
    let f = Fixture::new(json!({"code_change":true}));
    passed(f.freeze());
    refused(&f, f.publish(), "Only CHANGELOG.md");
    let f = Fixture::new(json!({"failed":true}));
    passed(f.freeze());
    refused(&f, f.publish(), "validation failed");
    let f = Fixture::new(json!({"red_during_validation":true}));
    passed(f.freeze());
    refused(&f, f.publish(), "Latest full gate on main is red");
}
