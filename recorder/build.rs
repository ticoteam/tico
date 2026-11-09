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
    println!("cargo:rerun-if-changed=src/capture/voice_processing.m");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .file("src/capture/voice_processing.m")
            .flag("-fobjc-arc")
            .flag("-fblocks")
            .flag("-mmacosx-version-min=14.2")
            .compile("tico_voice_processing");
        println!("cargo:rustc-link-lib=framework=AVFAudio");
        println!("cargo:rustc-link-lib=framework=AudioToolbox");
    }

    // Only the desktop app embeds the UI and needs Tauri's generated context.
    #[cfg(feature = "desktop")]
    tauri_build::build();
}
