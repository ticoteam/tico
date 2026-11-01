//! `tico-runner`: the Tico local runner. launchd and systemd units that start `python -m runner run` reach it through
//! runner/engine.py. It keeps the registration file, state directory, lock and outbox, and registers and checks this
//! computer itself (`enroll`, `adopt`, `doctor`, crate::commands).

// A turn's failure carries its whole report, and the caches key on tuples: both read better as they are.
#![allow(clippy::result_large_err, clippy::type_complexity)]
// baseline, remove as fixed
#![allow(clippy::await_holding_lock)]
#![allow(clippy::assertions_on_constants)]
#![allow(clippy::bool_assert_comparison)]
#![allow(clippy::cloned_ref_to_slice_refs)]
#![allow(clippy::collapsible_if)]
#![allow(clippy::manual_contains)]
#![allow(clippy::manual_unwrap_or_default)]
#![allow(clippy::nonminimal_bool)]
#![allow(clippy::needless_borrow)]
#![allow(clippy::single_match)]
#![allow(clippy::too_many_arguments)]
#![allow(clippy::unnecessary_cast)]
#![allow(clippy::unnecessary_lazy_evaluations)]
#![allow(clippy::useless_conversion)]
#![allow(clippy::useless_format)]

mod accounts;
mod afterwatch;
mod appdriver;
mod backlog;
mod browser_exit;
mod capture;
mod client;
mod cloud_machine;
mod commands;
mod config;
mod credential_imports;
mod credential_migration;
mod declared_access;
mod env;
mod events;
mod drive;
mod drain;
mod execute;
mod fallback;
mod flight;
mod follow;
mod fetchrepo;
mod gateway_relay;
mod gitrun;
#[cfg(test)]
mod gittests;
mod files_publish;
mod gitcred;
mod harness_memory;
mod harness_tools;
mod goals;
mod hosts;
mod lanes;
mod lease;
mod login_turns;
mod lock;
mod login;
mod profiles;
mod isolation;
mod turn_users;
mod checkout_groups;
mod mail_key;
mod manifest;
mod memory_history;
mod memory_tidy;
mod mcp;
mod model_subscriptions;
mod op;
mod panics;
mod perbot;
mod perf;
mod placement;
mod playbook;
mod product_files;
mod prompt;
mod reap;
mod continuity;
mod readiness;
mod readiness_extra;
mod redact;
mod relays;
mod repositories;
mod worktrees;
mod run_diagnostics;
mod runner;
mod sessions;
mod source_resume;
mod safegit;
mod screen;
mod sidejobs;
mod signin;
mod state;
mod tls;
mod subscription_refresh;
mod watchers;
mod util;
mod warm;
mod wire;

use std::path::PathBuf;
use std::sync::Arc;

use clap::{Parser, Subcommand};

/// The source fingerprint of this build (build.rs; runner/engine.py `source_id`).
pub const BUILD_ID: &str = env!("TICO_BUILD_ID");

#[derive(Parser)]
#[command(name = "tico-runner", about = "Run this computer's Tico bots: the Rust runner", version = runner::RUNNER_VERSION)]
struct Cli {
    /// The registration file `enroll` wrote.
    #[arg(long, global = true)]
    config: Option<PathBuf>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Register this computer with a one-time enrollment code.
    Enroll {
        #[arg(long)]
        url: String,
        #[arg(long)]
        code_file: PathBuf,
        #[arg(long)]
        label: String,
        #[arg(long)]
        projects: PathBuf,
        /// Environment id this registration belongs to.
        #[arg(long, default_value = "")]
        environment: String,
    },
    /// Claim and run this computer's turns until stopped.
    Run,
    /// Print this registration's assignments and eligible bots.
    Status,
    /// Check assigned repositories and executables without running bots.
    Doctor,
    /// Move eligible bots to this computer.
    Adopt {
        #[arg(long)]
        team: Option<String>,
        #[arg(long)]
        bot: Vec<String>,
    },
    /// Subscription profiles: one provider login per directory.
    Profile {
        #[command(subcommand)]
        action: ProfileAction,
    },
    /// Read this computer's flight file (turn timings, slow heartbeats and git commands).
    Flight {
        #[command(subcommand)]
        action: FlightAction,
    },
    /// This computer's screen for the web app (Linux): on, off, or where it stands. Off unless turned on here.
    Screen {
        #[command(subcommand)]
        action: ScreenAction,
    },
    /// Git's credential helper for one turn (git runs it; not for people).
    GitCredential {
        #[arg(long)]
        socket: Option<String>,
        #[arg(long)]
        bot: Option<String>,
        /// git's verb: only `get` answers.
        action: Option<String>,
    },
}

#[derive(Subcommand)]
enum FlightAction {
    /// Recent turns with their phase timings, the slowest phases, slow heartbeats and git commands.
    Summary {
        /// How far back: `90s`, `30m`, `1h`, `2d`.
        #[arg(long, default_value = "1h")]
        since: String,
        /// The folder holding flight.ndjson (default: this registration's state folder).
        #[arg(long)]
        dir: Option<PathBuf>,
    },
}

#[derive(Subcommand)]
enum ScreenAction {
    /// Offer a screen people can open from the web app (the hub's setting still decides who sees it).
    On,
    /// Stop the screen and close any open session.
    Off,
    /// Print whether it is on, what says so, and whether it can run here.
    Status,
}

/// `screen on|off|status`: the local lock in runner.json; a running runner follows it within seconds.
fn screen_main(action: ScreenAction, config: &mut config::Config) -> i32 {
    if let ScreenAction::On | ScreenAction::Off = action {
        let on = matches!(action, ScreenAction::On);
        config.data.insert("screen".into(), serde_json::Value::Bool(on));
        if let Err(e) = config.save() {
            fail(&e);
        }
    }
    let (on, source) = screen::lock(&config.path);
    let missing = if on { screen::missing_tools() } else { vec![] };
    let mut out = serde_json::json!({"screen": if on { "on" } else { "off" }, "set_by": source});
    if let Some(report) = screen::report(on, std::env::consts::OS, &missing, None, true) {
        out.as_object_mut().unwrap().extend(report.as_object().cloned().unwrap_or_default());
    }
    if source == "TICO_SCREEN" && !matches!(action, ScreenAction::Status) {
        out["note"] = serde_json::json!("TICO_SCREEN is set and wins over this setting");
    }
    if !missing.is_empty() && std::env::consts::OS == "linux" {
        out["note"] = serde_json::json!("Install xvfb x11vnc openbox (install.sh --screen does)");
    }
    println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
    0
}

#[derive(Subcommand)]
enum ProfileAction {
    /// Make a profile (the first one added is the default).
    Add {
        name: String,
        /// Use the operator's own logins.
        #[arg(long)]
        share_operator: bool,
    },
    /// Print the profiles and the default (the computer's login).
    List,
    /// Sign the computer's login (its default profile) in to one runtime, interactively.
    Login { name: String, runtime: String },
}

/// `profile add|list|login` (runner/__main__.py profile_command): local to this computer, no network.
fn profile_main(action: ProfileAction, config: &mut config::Config) -> i32 {
    let result = match action {
        ProfileAction::Add { name, share_operator } => profiles::command(config, "add", Some(&name), share_operator),
        ProfileAction::List => profiles::command(config, "list", None, false),
        ProfileAction::Login { name, runtime } => {
            let choices = harness_tools::profile_runtimes();
            if !choices.contains(&runtime) {
                fail(&format!("argument runtime: invalid choice: '{runtime}' (choose from {})", choices.join(", ")));
            }
            let entry = config.data.get("profiles").and_then(|p| p.get(&name)).filter(|e| util::truthy(Some(e))).cloned();
            let Some(entry) = entry else { fail("No such profile on this registration; add it with `profile add`") };
            // One login per tool per computer: only the computer's own (its default profile) signs in here.
            if config.data.get("default_profile").map(util::text_of).as_deref() != Some(name.as_str()) {
                fail(&format!("Profile {name} is not this computer's login: each computer has one login per AI tool, used by all its bots; for a different account, add another runner"));
            }
            let profile = profiles::Profile::new(&name, &util::text_of(&entry["dir"]), util::truthy(entry.get("share_operator")));
            if profile.share_operator {
                fail(&format!("Profile {name} shares the operator's own logins; sign in normally"));
            }
            let home = profile.home(&runtime).unwrap_or_default();
            let command = profiles::login_command(&runtime).unwrap_or_default();
            if command.is_empty() && harness_tools::caps(&runtime, "").api_keys == "bot" {
                let secrets = config.projects_dir().join("secrets");
                let key = harness_tools::api_key_env(&runtime).into_iter().next().unwrap_or_default();
                let name = harness_tools::name_for(&runtime);
                println!("{name} authenticates with an API key, not an interactive login: put {key} in {}/_shared.env or <bot>.env. This profile's {name} sessions live in {}.", secrets.display(), home.display());
                return 0;
            }
            if command.is_empty() || env::which(&command[0], &std::env::var("PATH").unwrap_or_default()).is_none() {
                fail(&format!("{} is not on PATH", command.first().unwrap_or(&runtime)));
            }
            println!("Signing in to {runtime} for profile {name} in {}", home.display());
            // Interactive on purpose: the CLI owns the browser flow and prints its own instructions.
            let status = std::process::Command::new(&command[0])
                .args(&command[1..])
                .env_clear()
                // The manifest's [env] too, e.g. MUSE_NO_AUTO_UPDATE: the runner updates its harnesses.
                .envs(profile.login_environment(&runtime, &std::env::vars().collect()))
                .status();
            return status.ok().and_then(|s| s.code()).unwrap_or(1);
        }
    };
    match result {
        Ok(text) => {
            println!("{text}");
            0
        }
        Err(e) => fail(&e),
    }
}

fn flight_summary(dir: &std::path::Path, since: &str) -> i32 {
    let Some(seconds) = flight::parse_since(since) else { fail(&format!("argument --since: not a duration: '{since}' (e.g. 30m, 1h, 2d)")) };
    print!("{}", flight::summary(dir, seconds, util::epoch_s()));
    0
}

fn fail(message: &str) -> ! {
    eprintln!("usage: tico-runner [--config CONFIG] {{enroll,run,status,doctor,adopt,profile,flight,screen}}\ntico-runner: error: {message}");
    std::process::exit(2)
}

fn main() {
    // A turn's `gh` is this binary under that name (gitcred::shim_dir).
    let argv: Vec<String> = std::env::args().collect();
    if argv.first().and_then(|a| std::path::Path::new(a).file_name()).is_some_and(|n| n == "gh") {
        std::process::exit(gitcred::gh_main(argv[1..].to_vec()));
    }
    if argv.get(1).is_some_and(|a| a == reap::FLAG) {
        std::process::exit(reap::main(&argv[2..]));
    }
    if argv.get(1).is_some_and(|a| a == "--release") {
        // The release this binary was built for (build.rs; "dev" for a source build). `--version` stays the protocol version.
        println!("{}", runner::RUNNER_RELEASE);
        std::process::exit(0);
    }
    if argv.get(1).is_some_and(|a| a == "--build-id") {
        // The source fingerprint build.rs stamped in: runner/engine.py rebuilds when the checkout's differs.
        println!("{}", BUILD_ID);
        std::process::exit(0);
    }
    let cli = Cli::parse();
    if let Command::GitCredential { socket, action, .. } = &cli.command {
        if action.as_deref().is_some_and(|a| a != "get") {
            std::process::exit(0);
        }
        std::process::exit(gitcred::git_credential_main(socket.clone()));
    }
    util::start_clock();
    if let Command::Flight { action: FlightAction::Summary { since, dir: Some(dir) } } = &cli.command {
        std::process::exit(flight_summary(dir, since));
    }
    let path = cli.config.unwrap_or_else(commands::default_config);
    if let Command::Enroll { url, code_file, label, projects, environment } = &cli.command {
        // Before Config::load: enrolling is what writes it.
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("tokio runtime");
        match rt.block_on(commands::enroll(&path, url, code_file, label, projects, environment)) {
            Ok(out) => println!("{}", util::py_dumps(&out)),
            Err((2, message)) => fail(&message),
            Err((code, message)) => {
                eprintln!("tico-runner: {message}");
                std::process::exit(code);
            }
        }
        std::process::exit(0);
    }
    let mut config = config::Config::load(&path).unwrap_or_else(|e| fail(&e));
    if let Command::Profile { action } = cli.command {
        std::process::exit(profile_main(action, &mut config));
    }
    if let Command::Screen { action } = cli.command {
        std::process::exit(screen_main(action, &mut config));
    }
    if let Command::Flight { action: FlightAction::Summary { since, .. } } = &cli.command {
        std::process::exit(flight_summary(&config.state_dir(), since));
    }
    if let Command::Run | Command::Doctor = cli.command {
        // Puts the tools bin on PATH, so it runs before any thread reads the environment. Doctor keeps its own
        // harness state, as Python's did, so a check never rewrites the running runner's.
        match cli.command {
            Command::Doctor => harness_tools::init_at(&config, path.parent().unwrap_or(std::path::Path::new(".")).join("harnesses-doctor.json")),
            _ => harness_tools::init(&config),
        };
        hosts::musecode::set_state_dir(&config.state_dir());
    }
    let rt = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().expect("tokio runtime");
    std::process::exit(rt.block_on(async move { start(cli.command, config).await }));
}

async fn start(command: Command, config: config::Config) -> i32 {
    let client = client::Client::new(&config.url(), &config.token(), 10, 1).unwrap_or_else(|e| fail(&e));
    let refused = |(code, message): commands::Refusal| {
        if code == 2 {
            fail(&message);
        }
        eprintln!("tico-runner: {message}");
        code
    };
    match &command {
        Command::Adopt { team, bot } => {
            // After a re-enrollment, the protected mail key follows this operator's previous registration once.
            mail_key::carry_protected(&config);
            return match commands::adopt(&config, &client, team.as_deref(), bot).await {
                Ok(lines) => {
                    lines.iter().for_each(|l| println!("{l}"));
                    0
                }
                Err(r) => refused(r),
            };
        }
        Command::Doctor => {
            mail_key::carry_protected(&config);
            return match commands::doctor(&config, &client).await {
                Ok((out, ready)) => {
                    println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
                    i32::from(!ready)
                }
                Err(r) => refused(r),
            };
        }
        _ => {}
    }
    if let Command::Status = command {
        let read = async { Ok::<_, client::ApiError>((client.get("runners/assignments").await?, client.get("runners/eligible").await?)) };
        return match read.await {
            Ok((assignments, eligible)) => {
                let out = serde_json::json!({"runner_id": config.runner_id(), "assignments": assignments, "eligible": eligible});
                println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
                0
            }
            Err(e) => {
                eprintln!("tico-runner: {}", runner::describe(&e));
                1
            }
        };
    }
    // After a re-enrollment, the protected mail key follows this operator's previous registration once.
    mail_key::carry_protected(&config);
    let state_dir = config.state_dir();
    if let Err(e) = std::fs::create_dir_all(&state_dir) {
        fail(&format!("cannot create {}: {e}", state_dir.display()));
    }
    let _lock = lock::acquire(&state_dir.join("runner.lock")).unwrap_or_else(|e| fail(&e));
    flight::init(&state_dir);
    panics::install();
    let state = state::State::open(&state_dir).unwrap_or_else(|e| fail(&e));
    // No turn runs yet: a company subscription's sign-in file left from one that was cut off goes.
    model_subscriptions::sweep(&state_dir);
    gateway_relay::sweep(&config.projects_dir());
    follow::record_revision(&state_dir);
    follow::init(&config.data, &state_dir);
    if isolation::enabled() {
        // Sign-ins a person ran as root (`docker exec`) go to the bot user, who runs the harnesses.
        let home = std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default());
        isolation::adopt_store(&[".codex", ".claude", ".claude.json", ".gemini", ".config"].map(|p| home.join(p)));
        hosts::grok::hand_login_to_bot();
        // Subscription profiles made before their homes were the bot user's, or by `profile add` without isolation.
        for profile in profiles::configured(&config.data) {
            if let Err(e) = profiles::hand_to_bot(&profile) {
                util::log(&format!("Tico runner: subscription profile {}: {e}", profile.name));
            }
        }
    }
    mail_key::init(&config); // an inbox bot's turn asks the credential socket for its mailbox's token
    let mut service = runner::Runner::new(config, client.clone(), state);
    match gitcred::CredentialSocket::serve(client.clone(), service.tokens.clone()) {
        Ok(socket) => service.socket = Some(socket),
        Err(e) => util::log(&format!("Tico runner: credential socket unavailable ({e}); turns use per-turn GitHub tokens")),
    }
    let service = Arc::new(service);
    let stopper = service.clone();
    tokio::spawn(async move {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).expect("SIGTERM handler");
        let mut int = signal(SignalKind::interrupt()).expect("SIGINT handler");
        if runner::drains_on_stop() {
            // SIGTERM: finish the running turns (Runner::shut_down); a second SIGTERM or a SIGINT stops at once.
            tokio::select! { _ = term.recv() => stopper.shut_down(), _ = int.recv() => {} }
        }
        tokio::select! { _ = term.recv() => {}, _ = int.recv() => {} }
        stopper.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        stopper.events.wake.notify_one();
    });
    util::log(&format!("Tico runner {} (rust) started for {}", runner::RUNNER_RELEASE, service.config.runner_id()));
    tls::announce(util::log);
    // No launchd here (TICO_SIDE_JOBS=1): the runner also supervises importers, close-calls and connectors.
    let side = sidejobs::start(&service.config);
    screen::start(service.config.path.clone(), client.clone());
    browser_exit::start(client.clone());
    service.run().await;
    screen::shutdown().await;
    if let Some((task, stop)) = side {
        stop.notify_one();
        let _ = task.await;
    }
    0
}

#[cfg(test)]
mod build_id_tests {
    /// build.rs's SHA-256 and file list agree with `ring` here and with runner/engine.py `source_id`.
    #[test]
    fn the_build_id_is_the_checkouts_source_id() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let mut names = vec!["runner-rs/build.rs".to_string(), "runner-rs/Cargo.toml".into(), "runner-rs/Cargo.lock".into()];
        let mut stack = vec![root.join("runner-rs/src")];
        while let Some(dir) = stack.pop() {
            for p in std::fs::read_dir(&dir).unwrap().flatten().map(|e| e.path()) {
                if p.is_dir() {
                    stack.push(p);
                } else {
                    names.push(p.strip_prefix(root).unwrap().display().to_string());
                }
            }
        }
        for p in std::fs::read_dir(root.join("runner/harnesses")).unwrap().flatten().map(|e| e.path()) {
            if p.extension().is_some_and(|e| e == "toml") {
                names.push(p.strip_prefix(root).unwrap().display().to_string());
            }
        }
        names.sort();
        let mut data = vec![];
        for name in names {
            let bytes = std::fs::read(root.join(&name)).unwrap();
            data.extend_from_slice(format!("{name}\0{}\0", bytes.len()).as_bytes());
            data.extend_from_slice(&bytes);
        }
        let digest = ring::digest::digest(&ring::digest::SHA256, &data);
        let id: String = digest.as_ref()[..8].iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(super::BUILD_ID, id);
        let python = std::process::Command::new("python3")
            .args(["-c", "import sys; from runner.engine import source_id; print(source_id(__import__('pathlib').Path(sys.argv[1])))"])
            .arg(root)
            .current_dir(root)
            .output();
        if let Ok(out) = python {
            assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
            assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), id);
        }
    }
}
