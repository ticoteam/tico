//! Tico Recorder's own updates. The app reads the signed release feed on launch and every six hours and
//! downloads a newer build in the background. Installing waits for `Recorder::update_hold` to clear, and it
//! never restarts the app by itself:
//! - the person clicks Restart: install and restart now (when nothing holds the app);
//! - no window has had focus for ten minutes: install, the new build starts at the next launch;
//! - the app quits with a downloaded build: install before exit, so the next launch is the new build.
//!
//! The page reads `status.update` from `view` and calls `update_check` and `update_install`.
use super::{Shared, lock_shared};
use serde_json::{Value, json};
use std::{
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, State, WindowEvent};
use tauri_plugin_updater::{Update, UpdaterExt};

/// The first check waits this long, so launch is not slowed down.
const FIRST_CHECK: Duration = Duration::from_secs(20);
const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
/// How often the background thread looks at a downloaded build.
const LOOK_EVERY: Duration = Duration::from_secs(30);
/// Without any focused window for this long, a downloaded build installs in the background.
pub const IDLE_BEFORE_INSTALL: Duration = Duration::from_secs(10 * 60);

/// Why an install is being considered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Intent {
    /// The background thread: install only after a long idle stretch, and never restart.
    Background,
    /// The person clicked Restart: install and restart.
    Restart,
    /// The app is quitting: install before exit, so the next launch is the new build.
    Quit,
}

/// What is waiting to be installed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Build {
    None,
    /// Downloaded and verified, not installed yet.
    Downloaded,
    /// Installed on disk; the next launch runs it.
    Installed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Wait,
    Install,
    InstallAndRestart,
}

/// The install policy as a pure decision. `hold` is `Recorder::update_hold`; `idle` is how long no window
/// has had focus (None while one has focus).
pub fn decide(intent: Intent, build: Build, hold: Option<&str>, idle: Option<Duration>) -> Step {
    if build == Build::None || hold.is_some() {
        return Step::Wait;
    }
    match intent {
        Intent::Restart => Step::InstallAndRestart,
        Intent::Background
            if build == Build::Downloaded && idle.is_some_and(|d| d >= IDLE_BEFORE_INSTALL) =>
        {
            Step::Install
        }
        Intent::Quit if build == Build::Downloaded => Step::Install,
        _ => Step::Wait,
    }
}

/// What `status.update` shows. Shared by the view, the check thread, window events and the commands.
#[derive(Clone)]
pub struct Updates(Arc<Mutex<Pending>>);

struct Pending {
    state: &'static str,
    version: String,
    notes: String,
    /// A downloaded build waiting to be installed.
    ready: Option<(Update, Vec<u8>)>,
    checking: bool,
    /// When no window had focus last; None while a window has focus.
    unfocused_since: Option<Instant>,
}

impl Default for Updates {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Pending {
            state: "none",
            version: String::new(),
            notes: String::new(),
            ready: None,
            checking: false,
            // The app starts unfocused; the first focus event clears this.
            unfocused_since: Some(Instant::now()),
        })))
    }
}

impl Updates {
    fn lock(&self) -> MutexGuard<'_, Pending> {
        self.0.lock().unwrap_or_else(|error| error.into_inner())
    }

    /// `status.update`. `blocked` is why an install waits right now (from `Recorder::update_hold`).
    pub fn status(&self, blocked: Option<&str>) -> Value {
        let pending = self.lock();
        let blocked = match pending.state {
            "ready" | "installed" => blocked.unwrap_or(""),
            _ => "",
        };
        json!({"state": pending.state, "version": pending.version,
            "notes": pending.notes, "blocked": blocked})
    }

    fn set(&self, app: &AppHandle, state: &'static str, version: &str, notes: &str) {
        {
            let mut pending = self.lock();
            pending.state = state;
            pending.version = version.into();
            pending.notes = notes.into();
        }
        // The window redraws from `view` after a change; this event is what asks it to.
        let _ = app.emit("changed", 0);
    }

    fn build(&self) -> Build {
        let pending = self.lock();
        if pending.state == "installed" {
            Build::Installed
        } else if pending.ready.is_some() {
            Build::Downloaded
        } else {
            Build::None
        }
    }

    fn idle_for(&self) -> Option<Duration> {
        self.lock().unfocused_since.map(|at| at.elapsed())
    }

    /// Tracks focus for the idle rule. A closed or hidden window counts as unfocused.
    pub fn window_event(&self, event: &WindowEvent) {
        let mut pending = self.lock();
        match event {
            WindowEvent::Focused(true) => pending.unfocused_since = None,
            WindowEvent::Focused(false)
            | WindowEvent::CloseRequested { .. }
            | WindowEvent::Destroyed => {
                pending.unfocused_since.get_or_insert_with(Instant::now);
            }
            _ => {}
        }
    }
}

/// The background thread: a check on launch and every six hours, and an install check every 30 seconds.
/// Runs whether or not the window is visible.
pub fn start(app: AppHandle, recorder: Shared, updates: Updates) {
    std::thread::Builder::new()
        .name("tico-recorder-update".into())
        .spawn(move || {
            std::thread::sleep(FIRST_CHECK);
            let mut checked: Option<Instant> = None;
            loop {
                if checked.is_none_or(|at| at.elapsed() >= CHECK_EVERY) {
                    checked = Some(Instant::now());
                    check(&app, &updates);
                }
                apply(&app, &recorder, &updates, Intent::Background);
                std::thread::sleep(LOOK_EVERY);
            }
        })
        .expect("recorder update thread");
}

/// Reads the feed and downloads a newer build. One check at a time, and a build already downloaded is kept.
fn check(app: &AppHandle, updates: &Updates) {
    {
        let mut pending = updates.lock();
        if pending.checking || pending.ready.is_some() || pending.state == "installed" {
            return;
        }
        pending.checking = true;
    }
    match tauri::async_runtime::block_on(async {
        let updater = app.updater().map_err(|error| error.to_string())?;
        updater.check().await.map_err(|error| error.to_string())
    }) {
        Ok(Some(update)) => {
            let version = update.version.clone();
            updates.set(app, "downloading", &version, "");
            match tauri::async_runtime::block_on(update.download(|_, _| {}, || {})) {
                Ok(bytes) => {
                    updates.lock().ready = Some((update, bytes));
                    updates.set(app, "ready", &version, "");
                }
                Err(error) => {
                    eprintln!("update download failed: {error}");
                    updates.set(
                        app,
                        "error",
                        &version,
                        "The update could not be downloaded. It will be tried again.",
                    );
                }
            }
        }
        Ok(None) => updates.set(app, "none", "", ""),
        Err(error) => {
            eprintln!("update check failed: {error}");
            updates.set(
                app,
                "error",
                "",
                "The update could not be checked. It will be tried again.",
            );
        }
    }
    updates.lock().checking = false;
}

/// Applies the install policy for `intent`. Returns true when it installed or restarted.
fn apply(app: &AppHandle, recorder: &Shared, updates: &Updates, intent: Intent) -> bool {
    // The hold check and the reservation happen under one recorder lock, so no recording, live meeting or
    // send can start in between; `updating` refuses them until the install is done. The lock itself is not
    // kept: quitting (the restart below, too) takes it on the main thread.
    let step = {
        let mut shared = lock_shared(recorder);
        let step = decide(
            intent,
            updates.build(),
            shared.update_hold(),
            updates.idle_for(),
        );
        if step != Step::Wait {
            shared.updating = true;
        }
        step
    };
    if step == Step::Wait {
        return false;
    }
    // Taken in its own statement: the Pending lock must be free again before `updates.set` below.
    let ready = updates.lock().ready.take();
    if let Some((update, bytes)) = ready {
        let version = update.version.clone();
        if let Err(error) = update.install(&bytes) {
            eprintln!("update install failed: {error}");
            updates.set(
                app,
                "error",
                &version,
                "The update could not be installed. It will be tried again.",
            );
            lock_shared(recorder).updating = false;
            return false;
        }
        updates.set(app, "installed", &version, "");
    }
    if step == Step::InstallAndRestart {
        // Restart is the last step: the process exits and the installed build starts. `updating` stays set.
        app.restart();
    }
    lock_shared(recorder).updating = false;
    true
}

/// Called when the app exits: installs a downloaded build if nothing holds the app. No restart.
pub fn on_quit(app: &AppHandle, recorder: &Shared, updates: &Updates) {
    apply(app, recorder, updates, Intent::Quit);
}

/// Checks now, from the page. A check already running is not doubled.
#[tauri::command(async)]
pub fn update_check(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
) -> Value {
    check(&app, &updates);
    updates.status(lock_shared(&recorder).update_hold())
}

/// Restart clicked: installs the downloaded build and restarts now, or says why it waits.
#[tauri::command(async)]
pub fn update_install(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
) -> Value {
    apply(&app, &recorder, &updates, Intent::Restart);
    updates.status(lock_shared(&recorder).update_hold())
}

#[cfg(test)]
mod tests {
    use super::*;

    const LONG: Option<Duration> = Some(Duration::from_secs(10 * 60));
    const SHORT: Option<Duration> = Some(Duration::from_secs(60));
    const HOLD: Option<&str> = Some("After this recording");

    #[test]
    fn nothing_downloaded_means_nothing_to_do() {
        for intent in [Intent::Background, Intent::Restart, Intent::Quit] {
            assert_eq!(decide(intent, Build::None, None, LONG), Step::Wait);
        }
    }

    #[test]
    fn a_hold_waits_for_every_intent() {
        for intent in [Intent::Background, Intent::Restart, Intent::Quit] {
            for build in [Build::Downloaded, Build::Installed] {
                assert_eq!(decide(intent, build, HOLD, LONG), Step::Wait);
            }
        }
    }

    #[test]
    fn restart_click_installs_and_restarts_when_free() {
        assert_eq!(
            decide(Intent::Restart, Build::Downloaded, None, SHORT),
            Step::InstallAndRestart
        );
        assert_eq!(
            decide(Intent::Restart, Build::Installed, None, None),
            Step::InstallAndRestart
        );
    }

    #[test]
    fn background_waits_until_ten_minutes_without_focus() {
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, None),
            Step::Wait
        );
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, SHORT),
            Step::Wait
        );
        assert_eq!(
            decide(Intent::Background, Build::Downloaded, None, LONG),
            Step::Install
        );
    }

    #[test]
    fn background_never_restarts_and_does_not_reinstall() {
        assert_eq!(
            decide(Intent::Background, Build::Installed, None, LONG),
            Step::Wait
        );
    }

    #[test]
    fn quit_installs_without_restart() {
        assert_eq!(
            decide(Intent::Quit, Build::Downloaded, None, None),
            Step::Install
        );
        assert_eq!(
            decide(Intent::Quit, Build::Installed, None, None),
            Step::Wait
        );
    }

    #[test]
    fn status_says_why_a_ready_build_waits_and_nothing_else() {
        let updates = Updates::default();
        assert_eq!(
            updates.status(Some("After this recording")),
            json!({
                "state": "none", "version": "", "notes": "", "blocked": ""
            })
        );
        updates.lock().state = "ready";
        assert_eq!(
            updates.status(Some("After this recording"))["blocked"],
            "After this recording"
        );
        assert_eq!(updates.status(None)["blocked"], "");
        updates.lock().state = "installed";
        assert_eq!(
            updates.status(Some("After the live meeting ends"))["blocked"],
            "After the live meeting ends"
        );
        updates.lock().state = "downloading";
        assert_eq!(updates.status(Some("After this recording"))["blocked"], "");
    }

    #[test]
    fn focus_resets_the_idle_clock() {
        let updates = Updates::default();
        updates.window_event(&WindowEvent::Focused(true));
        assert_eq!(updates.idle_for(), None);
        updates.window_event(&WindowEvent::Focused(false));
        assert!(updates.idle_for().is_some());
        updates.window_event(&WindowEvent::Focused(true));
        assert_eq!(updates.idle_for(), None);
    }
}
