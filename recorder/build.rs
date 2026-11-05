fn main() {
    // The release writes its version into tauri.conf.json before building (recorder.yml); the Recorder tells Tico
    // that version on its calls, the CLI included, so it is read here rather than from Cargo.toml.
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=build.rs");
    let config = std::fs::read_to_string("tauri.conf.json").unwrap_or_default();
    let version = config
        .split_once("\"version\"")
        .and_then(|(_, rest)| rest.split('"').nth(1))
        .filter(|value| !value.is_empty() && value.len() <= 40)
        .unwrap_or(env!("CARGO_PKG_VERSION"))
        .to_owned();
    println!("cargo:rustc-env=TICO_RECORDER_VERSION={version}");
    // Only the desktop app embeds the UI and needs Tauri's generated context.
    #[cfg(feature = "desktop")]
    tauri_build::build();
}
