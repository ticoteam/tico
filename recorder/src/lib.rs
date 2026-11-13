pub mod app_location;
pub mod assist;
pub mod audio;
pub mod call_end;
pub mod calls;
pub mod capture;
pub mod desktop;
pub mod diag;
#[cfg(feature = "diarize")]
pub mod diarize;
pub mod engine;
pub mod legacy;
pub mod live;
pub mod network;
pub mod notices;
pub mod qos;
pub mod retry;
pub mod settings;
pub mod signin;
pub mod speaker_names;
pub mod speech;
pub mod store;
pub mod stream;
pub mod task_links;
pub mod transcript;
pub mod workspace;
// build.rs's check of the window's build (dist/app against dist/build-manifest.txt), included here for its tests.
#[cfg(test)]
#[path = "../build_window.rs"]
mod build_window;
