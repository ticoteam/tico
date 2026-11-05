//! macOS 14.2+ Core Audio process tap. No screen/video capture or virtual audio driver.
use super::{AudioSink, CaptureStream, Source};
use crate::audio::Channel;
use anyhow::{Result, ensure};
use core_foundation::{
    array::CFArray,
    base::{CFType, TCFType},
    dictionary::CFDictionary,
    number::CFNumber,
    string::CFString,
};
use objc2::AnyThread;
use objc2_core_audio::*;
use objc2_core_audio_types::*;
use objc2_foundation::{NSArray, NSNumber, NSString};
use std::{
    ffi::c_void,
    ptr::{self, NonNull},
    sync::atomic::Ordering,
};

fn checked(status: i32, operation: &str) -> Result<()> {
    ensure!(
        status == 0,
        "{operation} failed (Core Audio {status}). Check macOS system-audio recording permission and the output device."
    );
    Ok(())
}
unsafe fn property<T>(
    object: u32,
    selector: u32,
    qualifier: *const c_void,
    qualifier_size: u32,
) -> Result<T> {
    let mut address = AudioObjectPropertyAddress {
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain,
    };
    let mut value = std::mem::MaybeUninit::<T>::zeroed();
    let mut bytes = std::mem::size_of::<T>() as u32;
    // SAFETY: addresses and sizes refer to initialized stack storage of the documented property type.
    checked(
        unsafe {
            AudioObjectGetPropertyData(
                object,
                NonNull::from(&mut address),
                qualifier_size,
                qualifier,
                NonNull::from(&mut bytes),
                NonNull::new(value.as_mut_ptr())
                    .expect("stack pointer")
                    .cast(),
            )
        },
        "Reading the audio device",
    )?;
    ensure!(
        bytes == std::mem::size_of::<T>() as u32,
        "The audio device returned an unexpected property size"
    );
    Ok(unsafe { value.assume_init() })
}

#[repr(C)]
struct Timebase {
    numer: u32,
    denom: u32,
}
unsafe extern "C" {
    fn mach_absolute_time() -> u64;
    fn mach_timebase_info(info: *mut Timebase) -> i32;
}
struct Callback {
    sink: AudioSink,
    origin: u64,
    numer: u32,
    denom: u32,
    rate: u32,
}

unsafe extern "C-unwind" fn receive(
    _device: u32,
    _now: NonNull<AudioTimeStamp>,
    input: NonNull<AudioBufferList>,
    input_time: NonNull<AudioTimeStamp>,
    _output: NonNull<AudioBufferList>,
    _output_time: NonNull<AudioTimeStamp>,
    context: *mut c_void,
) -> i32 {
    // Core Audio serializes a device's IOProc. The Box remains allocated until IO has stopped
    // and AudioDeviceDestroyIOProcID has succeeded; no other thread accesses its producer.
    let state = unsafe { &mut *context.cast::<Callback>() };
    let list = unsafe { input.as_ref() };
    let count = list.mNumberBuffers as usize;
    if count == 0 {
        return 0;
    }
    if count > 32 {
        state.sink.health.errors.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    // AudioBufferList is a C variable-length allocation, not a Rust fixed-size array.
    let buffers = unsafe {
        std::slice::from_raw_parts(ptr::addr_of!(list.mBuffers).cast::<AudioBuffer>(), count)
    };
    let mut frames = usize::MAX;
    let mut channels = 0;
    for buffer in buffers {
        if buffer.mData.is_null() || buffer.mNumberChannels == 0 {
            return 0;
        }
        let bytes = buffer.mDataByteSize as usize;
        if bytes % 4 != 0 {
            state.sink.health.errors.fetch_add(1, Ordering::Relaxed);
            return 0;
        }
        frames = frames.min(bytes / 4 / buffer.mNumberChannels as usize);
        channels += buffer.mNumberChannels as usize;
    }
    if channels == 0 || frames == 0 {
        return 0;
    }
    let timestamp = unsafe { input_time.as_ref() };
    if timestamp
        .mFlags
        .contains(AudioTimeStampFlags::HostTimeValid)
    {
        let ticks = timestamp.mHostTime.saturating_sub(state.origin);
        let frame = ((ticks as u128 * state.numer as u128 * state.rate as u128)
            / (state.denom as u128 * 1_000_000_000)) as u64;
        state.sink.set_frame(frame);
    }
    state.sink.push_frames(frames, |frame| {
        let mut sum = 0.;
        for buffer in buffers {
            // The tap format was validated as native-endian float32 before registering this callback.
            let data = unsafe {
                std::slice::from_raw_parts(
                    buffer.mData.cast::<f32>(),
                    buffer.mDataByteSize as usize / 4,
                )
            };
            let width = buffer.mNumberChannels as usize;
            sum += data[frame * width..(frame + 1) * width]
                .iter()
                .copied()
                .sum::<f32>();
        }
        sum / channels as f32
    });
    0
}

struct Tap {
    tap: u32,
    device: u32,
    procedure: AudioDeviceIOProcID,
    state: Option<Box<Callback>>,
    started: bool,
}
impl CaptureStream for Tap {
    fn stop(&mut self) -> Result<()> {
        let mut first_error = None;
        if self.started {
            let status = unsafe { AudioDeviceStop(self.device, self.procedure) };
            if status != 0 {
                first_error = Some(status);
            }
            self.started = false;
        }
        if self.procedure.is_some() {
            let status = unsafe { AudioDeviceDestroyIOProcID(self.device, self.procedure) };
            if status != 0 {
                first_error.get_or_insert(status);
                // A failed destroy does not prove callbacks have ceased. Retain their context
                // rather than risking use-after-free during a rare hardware teardown failure.
                if let Some(state) = self.state.take() {
                    Box::leak(state);
                }
            }
            self.procedure = None;
        }
        self.state.take();
        if self.device != 0 {
            let status = unsafe { AudioHardwareDestroyAggregateDevice(self.device) };
            if status != 0 {
                first_error.get_or_insert(status);
            }
            self.device = 0;
        }
        if self.tap != 0 {
            let status = unsafe { AudioHardwareDestroyProcessTap(self.tap) };
            if status != 0 {
                first_error.get_or_insert(status);
            }
            self.tap = 0;
        }
        if let Some(status) = first_error {
            checked(status, "Stopping meeting-audio capture")?;
        }
        Ok(())
    }
}
impl Drop for Tap {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

pub fn start() -> Result<Source> {
    // macOS calls require the application bundle's NSAudioCaptureUsageDescription. The packaged
    // app declares macOS 14.2 minimum. Permission refusal is returned as an explicit capture error.
    let pid = std::process::id() as i32;
    let process: u32 = unsafe {
        property(
            kAudioObjectSystemObject as u32,
            kAudioHardwarePropertyTranslatePIDToProcessObject,
            ptr::from_ref(&pid).cast(),
            std::mem::size_of_val(&pid) as u32,
        )
    }?;
    ensure!(
        process != 0,
        "Could not identify Recorder's own audio process for echo exclusion"
    );
    let excluded = NSArray::from_retained_slice(&[NSNumber::new_u32(process)]);
    let description = unsafe {
        CATapDescription::initStereoGlobalTapButExcludeProcesses(
            CATapDescription::alloc(),
            &excluded,
        )
    };
    unsafe {
        description.setName(&NSString::from_str("Tico Recorder meeting audio"));
        description.setPrivate(true);
        description.setMuteBehavior(CATapMuteBehavior::Unmuted);
    }
    let uid = unsafe { description.UUID().UUIDString() }.to_string();
    let mut tap = Tap {
        tap: 0,
        device: 0,
        procedure: None,
        state: None,
        started: false,
    };
    checked(
        unsafe { AudioHardwareCreateProcessTap(Some(&description), &mut tap.tap) },
        "Creating the meeting-audio tap",
    )?;
    let format: AudioStreamBasicDescription =
        unsafe { property(tap.tap, kAudioTapPropertyFormat, ptr::null(), 0) }?;
    ensure!(
        format.mFormatID == kAudioFormatLinearPCM
            && format.mFormatFlags & kAudioFormatFlagIsFloat != 0
            && format.mFormatFlags & kAudioFormatFlagIsBigEndian == 0
            && format.mBitsPerChannel == 32,
        "The system-audio tap did not provide native float32 PCM"
    );
    ensure!(
        format.mSampleRate >= 8000. && format.mSampleRate <= 192000.,
        "Invalid system-audio sample rate"
    );
    let pairs: Vec<(CFString, CFType)> = vec![
        (CFString::new("uid"), CFString::new(&uid).as_CFType()),
        (CFString::new("drift"), CFNumber::from(1_i32).as_CFType()),
    ];
    let tap_info = CFDictionary::from_CFType_pairs(&pairs);
    let taps = CFArray::from_CFTypes(&[tap_info]);
    let pairs: Vec<(CFString, CFType)> = vec![
        (
            CFString::new("name"),
            CFString::new("Tico Recorder private meeting input").as_CFType(),
        ),
        (
            CFString::new("uid"),
            CFString::new(&uuid::Uuid::new_v4().to_string()).as_CFType(),
        ),
        (CFString::new("private"), CFNumber::from(1_i32).as_CFType()),
        // Do not wait inside AudioDeviceStart for a meeting app to begin playing sound.
        (
            CFString::new("tapautostart"),
            CFNumber::from(0_i32).as_CFType(),
        ),
        (CFString::new("taps"), taps.as_CFType()),
    ];
    let dictionary = CFDictionary::from_CFType_pairs(&pairs);
    // Both Core Foundation bindings describe the same opaque CFDictionaryRef ABI. The
    // owned dictionary outlives this synchronous call, whose implementation retains it.
    let raw = unsafe {
        &*dictionary
            .as_concrete_TypeRef()
            .cast::<objc2_core_foundation::CFDictionary>()
    };
    checked(
        unsafe { AudioHardwareCreateAggregateDevice(raw, NonNull::from(&mut tap.device)) },
        "Creating the private meeting input",
    )?;
    let (sink, consumer) = AudioSink::channel();
    let health = sink.health.clone();
    let mut timebase = Timebase { numer: 0, denom: 0 };
    checked(
        unsafe { mach_timebase_info(&mut timebase) },
        "Reading the audio clock",
    )?;
    ensure!(timebase.denom != 0, "Invalid native audio clock");
    let started = std::time::Instant::now();
    let origin = unsafe { mach_absolute_time() };
    tap.state = Some(Box::new(Callback {
        sink,
        origin,
        numer: timebase.numer,
        denom: timebase.denom,
        rate: format.mSampleRate.round() as u32,
    }));
    let context = ptr::from_mut(tap.state.as_mut().expect("callback allocated").as_mut()).cast();
    checked(
        unsafe {
            AudioDeviceCreateIOProcID(
                tap.device,
                Some(receive),
                context,
                NonNull::from(&mut tap.procedure),
            )
        },
        "Registering meeting-audio capture",
    )?;
    checked(
        unsafe { AudioDeviceStart(tap.device, tap.procedure) },
        "Starting meeting-audio capture",
    )?;
    tap.started = true;
    Ok(Source {
        channel: Channel::Meeting,
        name: "System audio (Recorder excluded)".into(),
        sample_rate: format.mSampleRate.round() as u32,
        started,
        health,
        consumer,
        stream: Box::new(tap),
    })
}

unsafe extern "C" {
    fn tico_voice_start(
        selected: *const std::ffi::c_char,
        context: *mut c_void,
        receive: unsafe extern "C" fn(*mut c_void, *const *const f32, u32, u32, u64, f64),
        release: unsafe extern "C" fn(*mut c_void),
        rate: *mut f64,
    ) -> *mut c_void;
    fn tico_voice_stop(capture: *mut c_void);
}

struct VoiceInput {
    sink: AudioSink,
    origin: u64,
    numer: u32,
    denom: u32,
}
unsafe extern "C" fn voice_receive(
    context: *mut c_void,
    data: *const *const f32,
    channels: u32,
    frames: u32,
    host_time: u64,
    rate: f64,
) {
    // AVAudioEngine serializes the tap, and its block owns this context until the final callback.
    let state = unsafe { &mut *context.cast::<VoiceInput>() };
    if data.is_null() || channels == 0 || channels > 32 {
        return;
    }
    let data = unsafe { std::slice::from_raw_parts(data, channels as usize) };
    if data.iter().any(|p| p.is_null()) {
        return;
    }
    let nanos =
        host_time.saturating_sub(state.origin) as u128 * state.numer as u128 / state.denom as u128;
    state
        .sink
        .set_frame((nanos as f64 * rate / 1_000_000_000.) as u64);
    state.sink.push_frames(frames as usize, |i| {
        data.iter().map(|p| unsafe { *p.add(i) }).sum::<f32>() / channels as f32
    });
}
unsafe extern "C" fn voice_release(context: *mut c_void) {
    drop(unsafe { Box::from_raw(context.cast::<VoiceInput>()) });
}
struct VoiceCapture(*mut c_void);
// Only stop touches the handle after construction; the engine owns and serializes its callback.
unsafe impl Send for VoiceCapture {}
impl CaptureStream for VoiceCapture {
    fn stop(&mut self) -> Result<()> {
        if !self.0.is_null() {
            unsafe { tico_voice_stop(self.0) };
            self.0 = ptr::null_mut();
        }
        Ok(())
    }
}
impl Drop for VoiceCapture {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

pub fn start_voice_microphone(name: Option<&str>) -> Result<Source> {
    let selected = name.map(std::ffi::CString::new).transpose()?;
    let (sink, consumer) = AudioSink::channel();
    let health = sink.health.clone();
    let mut clock = Timebase { numer: 0, denom: 0 };
    checked(
        unsafe { mach_timebase_info(&mut clock) },
        "Reading the microphone clock",
    )?;
    ensure!(clock.denom != 0, "Invalid microphone clock");
    let started = std::time::Instant::now();
    let context = Box::into_raw(Box::new(VoiceInput {
        sink,
        origin: unsafe { mach_absolute_time() },
        numer: clock.numer,
        denom: clock.denom,
    }));
    // The bridge takes ownership on success and failure and sets rate before starting callbacks.
    let mut rate = 0.;
    let capture = unsafe {
        tico_voice_start(
            selected.as_ref().map_or(ptr::null(), |s| s.as_ptr()),
            context.cast(),
            voice_receive,
            voice_release,
            &mut rate,
        )
    };
    ensure!(
        !capture.is_null(),
        "Voice processing is unavailable for this microphone route"
    );
    let rate = rate as u32;
    Ok(Source {
        channel: Channel::Microphone,
        name: name.unwrap_or("Built-in microphone").into(),
        sample_rate: rate,
        started,
        health,
        consumer,
        stream: Box::new(VoiceCapture(capture)),
    })
}
