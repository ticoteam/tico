fn main() {
    // Only the desktop app embeds the UI and needs Tauri's generated context.
    #[cfg(feature = "desktop")]
    tauri_build::build();
}
