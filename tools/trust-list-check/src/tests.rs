use super::*;

fn lock(versions: &[(&str, &str)]) -> String {
    versions.iter().map(|(version, deps)| format!("[[package]]\nname = \"webpki-roots\"\nversion = \"{version}\"\nsource = \"registry+https://github.com/rust-lang/crates.io-index\"\n{deps}\n")).collect()
}
fn fixture(files: &[(&str, &str)]) -> BTreeMap<String, String> {
    files
        .iter()
        .map(|(p, s)| (p.to_string(), s.to_string()))
        .collect()
}
struct RegistryFixture {
    fail: bool,
    drop_root: bool,
}
impl Registry for RegistryFixture {
    fn latest(&mut self, package: &str) -> Result<String> {
        if self.fail {
            return Err("registry unavailable".into());
        }
        Ok(if package == "webpki-roots" {
            "1.0.11"
        } else {
            "2026.10.23"
        }
        .into())
    }
    fn roots(&mut self, _: &str, version: &str) -> Result<Roots> {
        let mut r = BTreeMap::from([("STILLTRUSTED".into(), "Example Root A".into())]);
        if self.drop_root && version != "1.0.11" && version != "2026.10.23" {
            r.insert("REMOVED".into(), "Example Removed Root".into());
        }
        Ok(r)
    }
}
#[derive(Default)]
struct HubFixture {
    tasks: Vec<Value>,
    writes: Vec<(Vec<String>, String)>,
    fail_write: bool,
    pages: usize,
}
impl Hub for HubFixture {
    fn call(&mut self, args: &[&str], key: Option<&str>) -> Result<Value> {
        if args == ["whoami"] {
            return Ok(
                json!({"actor": "bot:upgrades", "role": "bot", "run_id": "fixture-attempt", "runner_id": "fixture-runner"}),
            );
        }
        if args == ["api", "GET", "bots/upgrades"] {
            return Ok(json!({"state": "active", "assignment": {"runner_id": "fixture-runner"}}));
        }
        if args == ["api", "GET", "bots/engineering-lead"] {
            return Ok(json!({"state": "active"}));
        }
        if args.starts_with(&["api", "GET"]) {
            assert!(args[2].contains("snoozed=all"));
            self.pages += 1;
            // Put the existing task on page two, as on a larger engineering board.
            return Ok(if self.pages % 2 == 1 {
                json!({"tasks": [], "next_offset": 100})
            } else {
                json!({"tasks": self.tasks, "next_offset": null})
            });
        }
        let args: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        self.writes
            .push((args.clone(), key.expect("write idempotency key").into()));
        if self.fail_write {
            return Err("synthetic cloud refusal operation id: example".into());
        }
        let flag = |name: &str| {
            args.iter()
                .position(|s| s == name)
                .map(|i| args[i + 1].clone())
                .unwrap_or_default()
        };
        if args[1] == "create" {
            assert_eq!(flag("--owner"), OWNER);
            assert_eq!(flag("--type"), "Dev tickets");
            assert_eq!(flag("--step"), "On Deck");
            let task = json!({"id": format!("fixture-ticket-{}", self.tasks.len()), "body": flag("--body"), "title": flag("--title"), "note": "", "status": "open", "created": "2026-01-01"});
            self.tasks.push(task.clone());
            Ok(task)
        } else {
            assert_eq!(args[1], "update");
            let task = self.tasks.iter_mut().find(|t| t["id"] == args[2]).unwrap();
            task["note"] = json!(flag("--note"));
            task["title"] = json!(flag("--title"));
            Ok(task.clone())
        }
    }
}
fn report(files: &BTreeMap<String, String>, drop_root: bool) -> Report {
    scan(
        "fixture-revision".into(),
        inventory(files).unwrap(),
        &mut RegistryFixture {
            fail: false,
            drop_root,
        },
    )
    .unwrap()
}
#[test]
fn a1_stale_cargo_creates_one_dev_ticket_with_every_file_and_version() {
    let old = lock(&[("1.0.9", "")]);
    let files = fixture(&[
        ("app/Cargo.lock", &old),
        ("relay-rs/Cargo.lock", &old),
        ("runner-rs/Cargo.lock", &old),
        ("backend/requirements.txt", "certifi==2026.10.19"),
    ]);
    let mut hub = HubFixture::default();
    let r = report(&files, false);
    assert_eq!(apply(&r, &mut hub).unwrap()["action"], "created");
    assert_eq!(hub.writes.len(), 1);
    let body = hub.tasks[0]["body"].as_str().unwrap();
    for (file, _) in files {
        assert!(body.contains(&file));
    }
    for version in ["1.0.9", "1.0.11", "2026.10.19", "2026.10.23"] {
        assert!(body.contains(version));
    }
}
#[test]
fn a2_current_makes_no_cloud_calls_and_a6_shim_is_not_stale() {
    let cargo = lock(&[
        ("0.26.11", "dependencies = [\"webpki-roots 1.0.11\"]"),
        ("1.0.11", ""),
    ]);
    let files = fixture(&[
        ("runner-rs/Cargo.lock", &cargo),
        ("backend/requirements.txt", "certifi==2026.10.23"),
        (
            "pyproject.toml",
            "[project]\ndependencies=[\"certifi==2026.10.23\"]",
        ),
    ]);
    let r = report(&files, false);
    assert_eq!(r.checked.len(), 3);
    assert!(!r.checked.iter().any(|p| p.version == "0.26.11"));
    let mut hub = HubFixture::default();
    assert_eq!(apply(&r, &mut hub).unwrap()["action"], "none");
    assert!(hub.writes.is_empty());
    assert_eq!(hub.pages, 0);
}
#[test]
fn a3_new_stale_file_updates_same_ticket_including_page_two_and_snoozed_work() {
    let cargo = lock(&[("1.0.9", "")]);
    let mut files = fixture(&[("app/Cargo.lock", &cargo)]);
    let mut hub = HubFixture::default();
    apply(&report(&files, false), &mut hub).unwrap();
    files.insert(
        "extra/requirements-test.txt".into(),
        "certifi==2026.10.19".into(),
    );
    let r = report(&files, false);
    assert_eq!(apply(&r, &mut hub).unwrap()["action"], "updated");
    assert_eq!(hub.tasks.len(), 1);
    assert!(hub.tasks[0]["note"]
        .as_str()
        .unwrap()
        .contains("extra/requirements-test.txt"));
    assert_eq!(
        hub.writes.iter().filter(|(a, _)| a[1] == "create").count(),
        1
    );
    assert_eq!(apply(&r, &mut hub).unwrap()["action"], "unchanged");
    assert_eq!(hub.writes.len(), 2);
}
#[test]
fn a4_removed_roots_escalate_existing_ticket_and_do_not_erase_evidence() {
    let files = fixture(&[("backend/requirements.txt", "certifi==2026.10.19")]);
    let mut hub = HubFixture::default();
    apply(&report(&files, false), &mut hub).unwrap();
    apply(&report(&files, true), &mut hub).unwrap();
    assert!(hub.tasks[0]["title"]
        .as_str()
        .unwrap()
        .starts_with("URGENT:"));
    let note = hub.tasks[0]["note"].as_str().unwrap();
    assert!(note.contains("Example Removed Root"));
    assert!(note.contains("Bump and ship in the next release"));
    apply(&report(&files, false), &mut hub).unwrap();
    assert!(hub.tasks[0]["title"]
        .as_str()
        .unwrap()
        .starts_with("URGENT:"));
    assert!(hub.tasks[0]["note"]
        .as_str()
        .unwrap()
        .contains("Example Removed Root"));
    assert_eq!(
        apply(&report(&files, false), &mut hub).unwrap()["action"],
        "unchanged"
    );
}
#[test]
fn pin_inventory_handles_multiversion_pre_shim_and_python_dependency_formats() {
    let cargo = lock(&[("0.26.0", ""), ("1.0.11", "")]);
    let files = fixture(&[("runner-rs/Cargo.lock", &cargo), ("a/requirements-dev.txt", " # certifi==1999.1.1\n CERTIFI [extra] == 2026.10.19 ; python_version > '3'\\\n --hash=sha256:example"), ("b/pyproject.toml", "[tool.poetry.dependencies]\ncertifi={version=\"=2026.10.23\"}\n[dependency-groups]\ntest=[\"certifi==2026.10.19\"]")]);
    let pins = inventory(&files).unwrap();
    assert_eq!(pins.len(), 5);
    assert!(pins.iter().any(|p| p.version == "0.26.0"));
    assert!(report(&files, false)
        .findings
        .iter()
        .any(|f| f.pin.version == "0.26.0"));
    assert!(inventory(&fixture(&[("requirements.txt", "certifi>=2026.1.1")])).is_err());
}
#[test]
fn actual_pem_bytes_detect_same_count_replacements_and_fail_on_format_drift() {
    let old = "# Subject: Example A\n# Label: \"Root A\"\n-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n";
    let rust = old.replace("#", " *") + "TrustAnchor {\n";
    let parsed = roots(&rust, true).unwrap();
    assert_eq!(parsed["QUJD"], "Root A");
    let new = roots(&old.replace("QUJD", "REVG"), false).unwrap();
    assert_eq!(parsed.len(), new.len());
    assert_eq!(parsed.keys().filter(|k| !new.contains_key(*k)).count(), 1);
    assert!(roots("TrustAnchor { broken format", true).is_err());
    assert!(roots(&(rust + "TrustAnchor {"), true).is_err());
}
#[test]
fn failed_scan_or_write_never_becomes_current_and_never_retries() {
    let files = fixture(&[("requirements.txt", "certifi==2026.10.19")]);
    assert!(scan(
        "fixture".into(),
        inventory(&files).unwrap(),
        &mut RegistryFixture {
            fail: true,
            drop_root: false
        }
    )
    .is_err());
    let mut hub = HubFixture {
        fail_write: true,
        ..Default::default()
    };
    assert!(apply(&report(&files, true), &mut hub)
        .unwrap_err()
        .contains("synthetic cloud refusal"));
    assert_eq!(hub.writes.len(), 1);
}
#[test]
fn same_attempt_retries_share_key_and_completed_ticket_starts_a_new_generation() {
    let r = report(
        &fixture(&[("requirements.txt", "certifi==2026.10.19")]),
        false,
    );
    let (mut a, mut b) = (HubFixture::default(), HubFixture::default());
    apply(&r, &mut a).unwrap();
    apply(&r, &mut b).unwrap();
    assert_eq!(a.writes[0].1, b.writes[0].1);
    a.tasks[0]["status"] = json!("closed");
    apply(&r, &mut a).unwrap();
    assert_ne!(a.writes[0].1, a.writes[1].1);
}

#[test]
fn a5_stale_scan_succeeds_and_has_no_pr_workflow_caller() {
    let r = scan(
        "fixture".into(),
        inventory(&fixture(&[("requirements.txt", "certifi==2026.10.19")])).unwrap(),
        &mut RegistryFixture {
            fail: false,
            drop_root: true,
        },
    );
    assert!(!r.unwrap().findings.is_empty()); // Staleness is data, not a failing check.
    let workflows = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.github/workflows");
    for entry in std::fs::read_dir(workflows).unwrap() {
        let path = entry.unwrap().path();
        if path.is_file() {
            let workflow = std::fs::read_to_string(path).unwrap();
            assert!(
                !workflow.contains("trust-list-check"),
                "Run only via the daily Upgrades routine"
            );
        }
    }
}

#[test]
fn python_metadata_is_not_a_dependency_declaration() {
    let files = fixture(&[("pyproject.toml", "[project]\ndescription=\"certifi helps validate TLS\"\ndependencies=[\"certifi==2026.10.23\"]\n[tool.uv.sources]\ncertifi={index=\"example\"}")]);
    assert_eq!(inventory(&files).unwrap().len(), 1);
}

#[test]
fn assigned_runner_serializes_distinct_attempts_escalates_and_renews() {
    use std::sync::{mpsc, Arc, Mutex};
    let scratch = std::env::temp_dir().join(format!("tico-trust-lock-test-{}", process::id()));
    std::fs::create_dir_all(&scratch).unwrap();
    let lock_path = scratch.join("writer.lock");
    let probe = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)
        .unwrap();
    let state = Arc::new(Mutex::new(HubFixture::default()));
    let (listed_tx, listed_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let (second_done_tx, second_done_rx) = mpsc::channel();
    struct Attempt {
        state: Arc<Mutex<HubFixture>>,
        id: &'static str,
        pause: Option<(mpsc::Sender<()>, mpsc::Receiver<()>)>,
    }
    impl Hub for Attempt {
        fn call(&mut self, args: &[&str], key: Option<&str>) -> Result<Value> {
            if args == ["whoami"] {
                return Ok(
                    json!({"actor": "bot:upgrades", "role": "bot", "run_id": self.id, "runner_id": "fixture-runner"}),
                );
            }
            // Match the real cloud's attempt-scoped receipts and atomic title duplicate guard.
            let scoped_key = key.map(|key| format!("{}:{key}", self.id));
            let value = {
                let mut state = self.state.lock().unwrap();
                if args.starts_with(&["task", "create"]) {
                    let title = args[args.iter().position(|a| *a == "--title").unwrap() + 1];
                    if state
                        .tasks
                        .iter()
                        .any(|t| t["title"] == title && t["status"] == "open")
                    {
                        return Err("synthetic duplicate refusal".into());
                    }
                }
                state.call(args, scoped_key.as_deref())?
            };
            if args.starts_with(&["api", "GET"])
                && args[2].starts_with("tasks?")
                && value["next_offset"].is_null()
            {
                if let Some((listed, release)) = self.pause.take() {
                    listed.send(()).unwrap();
                    release.recv_timeout(Duration::from_secs(5)).unwrap();
                }
            }
            Ok(value)
        }
    }
    let files = fixture(&[("requirements.txt", "certifi==2026.10.19")]);
    let routine = report(&files, false);
    let urgent = report(&files, true);
    let a_state = state.clone();
    let a_path = lock_path.clone();
    let first = std::thread::spawn(move || {
        apply_at(
            &routine,
            &mut Attempt {
                state: a_state,
                id: "attempt-A",
                pause: Some((listed_tx, release_rx)),
            },
            &a_path,
        )
        .unwrap()
    });
    listed_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    assert!(
        matches!(probe.try_lock(), Err(std::fs::TryLockError::WouldBlock)),
        "discovery must hold the shared writer lock"
    );
    let b_state = state.clone();
    let b_path = lock_path.clone();
    let second = std::thread::spawn(move || {
        let value = apply_at(
            &urgent,
            &mut Attempt {
                state: b_state,
                id: "attempt-B",
                pause: None,
            },
            &b_path,
        );
        second_done_tx.send(()).unwrap();
        value.unwrap()
    });
    // A is paused after its empty cloud list. B cannot discover/create until A releases the lock.
    assert!(second_done_rx
        .recv_timeout(Duration::from_millis(300))
        .is_err());
    release_tx.send(()).unwrap();
    assert_eq!(first.join().unwrap()["action"], "created");
    assert_eq!(second.join().unwrap()["action"], "updated");
    {
        let mut saved = state.lock().unwrap();
        assert_eq!(saved.tasks.len(), 1);
        assert!(saved.tasks[0]["title"]
            .as_str()
            .unwrap()
            .starts_with("URGENT:"));
        assert!(saved.tasks[0]["note"]
            .as_str()
            .unwrap()
            .contains("Example Removed Root"));
        assert!(saved.writes[0].1.starts_with("attempt-A:"));
        assert!(saved.writes[1].1.starts_with("attempt-B:"));
        saved.tasks[0]["status"] = json!("closed");
    }
    let later = apply_at(
        &report(&files, false),
        &mut Attempt {
            state: state.clone(),
            id: "attempt-C",
            pause: None,
        },
        &lock_path,
    )
    .unwrap();
    assert_eq!(later["action"], "created");
    let saved = state.lock().unwrap();
    assert_eq!(saved.tasks.len(), 2);
    assert_eq!(
        saved.tasks.iter().filter(|t| t["status"] == "open").count(),
        1
    );
    assert_ne!(saved.tasks[0]["id"], saved.tasks[1]["id"]);
    println!("distinct attempts A/B: one open ticket, urgent root evidence retained; C after closure: one new open lifecycle");
    drop(saved);
    std::fs::remove_dir_all(scratch).unwrap();
}

#[test]
fn nonassigned_or_external_writers_are_rejected_before_discovery() {
    struct Unassigned {
        discovered: bool,
    }
    impl Hub for Unassigned {
        fn call(&mut self, args: &[&str], _: Option<&str>) -> Result<Value> {
            if args == ["whoami"] {
                return Ok(
                    json!({"actor": "bot:upgrades", "role": "bot", "run_id": "fixture-attempt", "runner_id": "previous-runner"}),
                );
            }
            if args == ["api", "GET", "bots/upgrades"] {
                return Ok(json!({"state": "active", "assignment": {"runner_id": "new-runner"}}));
            }
            self.discovered = true;
            panic!("no task discovery or write is allowed");
        }
    }
    let mut hub = Unassigned { discovered: false };
    let r = report(
        &fixture(&[("requirements.txt", "certifi==2026.10.19")]),
        false,
    );
    assert!(apply(&r, &mut hub)
        .unwrap_err()
        .contains("assigned runner lease"));
    assert!(!hub.discovered);
}
