fn main() {
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
