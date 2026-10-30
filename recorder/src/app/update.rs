//! Tico Recorder's own updates. The app reads the signed release feed on launch and every six hours,
//! downloads a newer build in the background, and installs it once `Recorder::update_hold` is clear: no
//! recording running and no live meeting connected. Restart does not override that hold; it waits too.
//! The page reads `status.update` from `view` and calls `update_check` and `update_install`.
use super::{Shared, lock_shared};
use serde_json::{Value, json};
use std::{
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_updater::{Update, UpdaterExt};

/// The first check waits this long, so launch is not slowed down.
const FIRST_CHECK: Duration = Duration::from_secs(20);
const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
/// How often a downloaded build is offered for install while the recorder is busy.
const IDLE_EVERY: Duration = Duration::from_secs(30);

/// What `status.update` shows. Shared by the view, the check thread and the commands.
#[derive(Clone)]
pub struct Updates(Arc<Mutex<Pending>>);

struct Pending {
    state: &'static str,
    version: String,
    notes: String,
    /// A downloaded build waiting for an idle moment to install.
    ready: Option<(Update, Vec<u8>)>,
    checking: bool,
}

impl Default for Updates {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Pending {
            state: "none",
            version: String::new(),
            notes: String::new(),
            ready: None,
            checking: false,
        })))
    }
}

impl Updates {
    fn lock(&self) -> MutexGuard<'_, Pending> {
        self.0.lock().unwrap_or_else(|error| error.into_inner())
    }

    /// `status.update`. `blocked` is why a downloaded build waits right now (from `Recorder::update_hold`).
    pub fn status(&self, blocked: Option<&str>) -> Value {
        let pending = self.lock();
        let blocked = match pending.state {
            "ready" => blocked.unwrap_or(""),
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
}

/// The background thread: a check on launch and every six hours, and an install whenever one is ready and
/// the recorder is idle. Runs whether or not the window is visible.
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
                install_if_idle(&app, &recorder, &updates);
                std::thread::sleep(IDLE_EVERY);
            }
        })
        .expect("recorder update thread");
}

/// Reads the feed and downloads a newer build. One check at a time, and a build already downloaded is kept.
fn check(app: &AppHandle, updates: &Updates) {
    {
        let mut pending = updates.lock();
        if pending.checking || pending.ready.is_some() {
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

/// Installs the downloaded build and restarts, once no recording or live meeting holds the app. Returns
/// false when it waited. A restart is the last step: the process exits and the new build starts.
fn install_if_idle(app: &AppHandle, recorder: &Shared, updates: &Updates) -> bool {
    if lock_shared(recorder).update_hold().is_some() {
        return false;
    }
    let Some((update, bytes)) = updates.lock().ready.take() else {
        return false;
    };
    let version = update.version.clone();
    match update.install(&bytes) {
        Ok(()) => app.restart(),
        Err(error) => {
            eprintln!("update install failed: {error}");
            updates.set(
                app,
                "error",
                &version,
                "The update could not be installed. It will be tried again.",
            );
            false
        }
    }
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

/// Installs the downloaded build now if the recorder is idle; otherwise it says why it waits.
#[tauri::command(async)]
pub fn update_install(
    app: AppHandle,
    recorder: State<'_, Shared>,
    updates: State<'_, Updates>,
) -> Value {
    install_if_idle(&app, &recorder, &updates);
    updates.status(lock_shared(&recorder).update_hold())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_says_why_a_ready_build_waits_and_nothing_else() {
        let updates = Updates::default();
        assert_eq!(
            updates.status(Some("After this recording")),
            json!({
                "state": "none", "version": "", "notes": "", "blocked": ""
            })
        );
        {
            let mut pending = updates.lock();
            pending.state = "ready";
            pending.version = "0.2.1".into();
        }
        assert_eq!(
            updates.status(Some("After this recording"))["blocked"],
            "After this recording"
        );
        assert_eq!(updates.status(None)["blocked"], "");
        updates.lock().state = "downloading";
        assert_eq!(updates.status(Some("After this recording"))["blocked"], "");
    }
}
