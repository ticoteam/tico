//! Thread priority classes and memory hints on macOS. Background work runs at utility or background QoS, so the
//! system keeps it on efficiency cores and out of the way; audio capture and the audio writer
//! keep their default (higher) class and are never lowered.

/// Work someone is waiting on but not watching: live sync, transcription, drafting, live text.
pub fn utility() {
    #[cfg(target_os = "macos")]
    set(0x11);
}
/// Work nobody waits on: speaker labels after a meeting, the heaviest burst.
pub fn background() {
    #[cfg(target_os = "macos")]
    set(0x09);
}

#[cfg(target_os = "macos")]
fn set(class: u32) {
    unsafe extern "C" {
        fn pthread_set_qos_class_self_np(class: u32, relative_priority: i32) -> i32;
    }
    // SAFETY: changes only the calling thread's scheduling class; failure leaves it unchanged.
    unsafe {
        pthread_set_qos_class_self_np(class, 0);
    }
}

/// Hands memory freed by a finished burst (a recording, speaker labels) back to the system.
/// The allocator otherwise keeps freed pages mapped, so the app would stay at its peak size.
pub fn release_memory() {
    #[cfg(target_os = "macos")]
    {
        unsafe extern "C" {
            fn malloc_zone_pressure_relief(zone: *mut std::ffi::c_void, goal: usize) -> usize;
        }
        // SAFETY: a null zone means every zone; goal 0 releases as much as possible.
        unsafe {
            malloc_zone_pressure_relief(std::ptr::null_mut(), 0);
        }
    }
}
